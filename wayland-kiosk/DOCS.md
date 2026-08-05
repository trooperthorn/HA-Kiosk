# HAOS Wayland Kiosk

A streamlined, hardware-accelerated kiosk integration for Home Assistant Operating System.

By replacing the aging X11 stack with a direct Wayland compositor (Cage), this add-on provides a robust, tear-free environment for rendering Chromium dashboards on your local hardware.

## Configuration Options

* **ha_url**: The URL the kiosk should load on boot. (Default: `http://supervisor/core`)
* **rotate_display**: Hardware rotation mapping. Supports `normal`, `right` (90°), `inverted` (180°), or `left` (270°).
* **ignore_certificate_errors**: Set to `true` if your URL relies on self-signed SSL certificates.

## REST API Server

This add-on spins up a background API server allowing you to control the screen state dynamically from Home Assistant automations:
* `display_on`: Wakes up the monitor.
* `display_off`: Powers down the monitor output via `wlr-randr`.
* `refresh_browser`: Triggers an active reload of the Chromium dashboard.
* `launch_url`: Uses the Chrome DevTools Protocol to seamlessly navigate to a new page.
