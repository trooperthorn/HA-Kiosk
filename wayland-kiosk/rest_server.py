import shlex
import logging
import asyncio
import urllib.request
import urllib.error
import json
import subprocess
from typing import Any, Dict
from aiohttp import web

# Type alias for your payload
Payload = Dict[str, Any]
SHORT_TIMEOUT = 5

# Security Whitelist for execution command wrapper
ALLOWED_COMMANDS = {"wlr-randr", "wtype", "killall", "swayidle"}
DANGEROUS_SHELL_TOKENS = [";", "|", "&", ">", "<", "$", "`"]

# --------------------------------------------------------------------------- #
# SERVER ROUTING
# --------------------------------------------------------------------------- #
ROUTES = {}

def register_function(name, optional=None, required=None, validators=None):
    """Stores the registered functions into the ROUTES dictionary."""
    def decorator(func):
        ROUTES[name] = func
        return func
    return decorator

async def execute_command(cmd_list, timeout=SHORT_TIMEOUT, log_prefix="", allow_command=False, print_stdout=True):
    """Your updated safe execution wrapper enforcing the Wayland whitelist"""
    if not allow_command or cmd_list[0] not in ALLOWED_COMMANDS:
        return {"success": False, "error": f"Command {cmd_list[0]} not whitelisted."}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_list, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {"success": proc.returncode == 0, "stdout": stdout.decode(), "stderr": stderr.decode()}
    except Exception as e:
        return {"success": False, "error": str(e)}

#CHROME WATCH
async def chromium_watchdog():
    """Polls the CDP endpoint to verify Chromium is not frozen."""
    failures = 0
    # Give Chromium 20 seconds to finish its initial boot sequence
    await asyncio.sleep(20) 
    
    while True:
        try:
            # Ping the CDP endpoint with a strict 5-second timeout
            def _ping():
                req = urllib.request.Request("http://localhost:9222/json")
                urllib.request.urlopen(req, timeout=5)
            
            await asyncio.to_thread(_ping)
            failures = 0 # Reset counter on success
            
        except Exception as e:
            failures += 1
            logging.warning(f"Watchdog: Chromium unresponsive ({failures}/3).")
            
            if failures >= 3:
                logging.error("Watchdog: Chromium is frozen! Forcing container restart.")
                # Killing Cage drops PID 1, stopping the container and triggering the HA Watchdog
                subprocess.run(["killall", "cage"])
                break
                
        await asyncio.sleep(30)

# --------------------------------------------------------------------------- #
# WAYLAND API ENDPOINTS
# --------------------------------------------------------------------------- #

@register_function("refresh_browser")
async def handle_refresh_browser(data: Payload) -> dict[str, Any]:
    """Send F5 to refresh browser via wtype."""
    result = await execute_command(["wtype", "-k", "F5"], 
                                   timeout=SHORT_TIMEOUT, log_prefix="refresh_browser", allow_command=True)
    return {"success": result["success"]}


@register_function("is_display_on")
async def handle_is_display_on(data: Payload) -> dict[str, Any]:
    """Return boolean whether monitor is currently on."""
    result = await execute_command(["wlr-randr"], print_stdout=False, 
                                   timeout=SHORT_TIMEOUT, log_prefix="is_display_on", allow_command=True)
    if not result["success"]:
        return {"success": False, "error": "Failed to query display state"}

    is_on = "Enabled: yes" in result["stdout"]
    logging.info("[is_display_on] Monitor is %s", "ON" if is_on else "OFF")
    return {"success": True, "display_on": is_on}


@register_function("display_on", optional=["timeout"])
async def handle_display_on(data: Payload) -> dict[str, Any]:
    """Turn display on, optionally set swayidle blanking timeout."""
    blank_timeout = data.get("timeout")
    cmds = [["wlr-randr", "--output", "*", "--on"]]
    log_msg = ""
    
    if blank_timeout is None:
        pass
    elif blank_timeout == 0:
        cmds += [["killall", "swayidle"]]
        log_msg = " Screen timeout disabled"
    elif blank_timeout > 0:
        t = str(blank_timeout)
        cmds += [
            ["killall", "swayidle"],
            ["swayidle", "-w", "timeout", t, "wlr-randr --output * --off", "resume", "wlr-randr --output * --on"]
        ]
        log_msg = f" Screen timeout: {blank_timeout}s"

    results = [await execute_command(cmd, timeout=SHORT_TIMEOUT, log_prefix="display_on", allow_command=True) for cmd in cmds]
    logging.info("[display_on]%s", log_msg)
    return {"success": all(r["success"] for r in results), "results": results}


@register_function("display_off")
async def handle_display_off(data: Payload) -> dict[str, Any]:
    """Force display off immediately using Wayland."""
    result = await execute_command(["wlr-randr", "--output", "*", "--off"], 
                                   timeout=SHORT_TIMEOUT, log_prefix="display_off", allow_command=True)
    return {"success": result["success"]}


@register_function("wlr_randr", required=["args"])
async def handle_wlr_randr(data: Payload) -> dict[str, Any]:
    """Run arbitrary wlr-randr command (sanitized). Replaces handle_xset."""
    args = data["args"]
    dangerous_tokens = [tok for tok in DANGEROUS_SHELL_TOKENS if tok in args]
    if dangerous_tokens:
        return {"success": False, "error": f"Forbidden shell metacharacters: {dangerous_tokens}"}
    
    args_list = shlex.split(args)  
    result = await execute_command(["wlr-randr"] + args_list, timeout=SHORT_TIMEOUT, log_prefix="wlr_randr", allow_command=True)
    return {"success": result["success"], "result": result}


@register_function("launch_url", optional=["url"])
async def handle_launch_url(data: Payload) -> dict[str, Any]:
    """Redirect browser with given URL via Chrome DevTools Protocol."""
    url = str(data["url"]) if data.get("url") else "http://supervisor/core"
    if url != "about:blank" and not url.startswith(("http://", "https://")):
        url = "http://" + url
        
    def _send_cdp_request():
        try:
            req = urllib.request.Request("http://localhost:9222/json")
            with urllib.request.urlopen(req) as response:
                pages = json.loads(response.read())
            if not pages:
                return False
                
            target_id = pages[0]['id']
            activate_req = urllib.request.Request(f"http://localhost:9222/json/activate/{target_id}", method='PUT')
            urllib.request.urlopen(activate_req)
            return True
        except urllib.error.URLError as e:
            logging.error(f"CDP Request failed: {e}")
            return False

    success = await asyncio.to_thread(_send_cdp_request)
    return {"success": success}

# --------------------------------------------------------------------------- #
# SERVER INITIALIZATION & WATCHDOG EXECUTION
# --------------------------------------------------------------------------- #


async def api_handler(request):
    """Handles incoming POST requests and routes them to the correct function."""
    try:
        data = await request.json()
        command = data.get("command")
        
        if command in ROUTES:
            # Execute the matched async function
            result = await ROUTES[command](data)
            return web.json_response(result)
        else:
            return web.json_response({"success": False, "error": f"Unknown command: {command}"}, status=400)
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logging.info("Starting HAOS-Wayland-Kiosk REST API...")
    
    # 1. Start the Chrome Watchdog in the background
    asyncio.create_task(chromium_watchdog())
    logging.info("Chromium Watchdog initialized.")
    
    # 2. Initialize the Web Server
    app = web.Application()
    app.router.add_post('/api', api_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Bind to port 8080
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    
    logging.info("API listening on port 8080. Ready for Home Assistant commands.")
    
    # Run forever
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
