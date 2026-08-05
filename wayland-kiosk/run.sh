#!/usr/bin/env bashio

bashio::log.info "================ SYSTEM DIAGNOSTICS ================"

# 1. Check GPU / DRM devices
if [ -d "/dev/dri" ]; then
    bashio::log.info "DRM Device Nodes Found:"
    ls -la /dev/dri
else
    bashio::log.warning "NO /dev/dri DIRECTORY FOUND! GPU passthrough is missing."
fi

# 2. Scan physical display connectors in /sys/class/drm
bashio::log.info "Scanning Connected Displays:"
if [ -d "/sys/class/drm" ]; then
    for status_file in /sys/class/drm/*/status; do
        if [ -f "$status_file" ]; then
            card=$(echo "$status_file" | cut -d'/' -f5)
            status=$(cat "$status_file")
            bashio::log.info "  Output Connector [$card]: $status"
        fi
    done
else
    bashio::log.warning "No /sys/class/drm connector entries found!"
fi

# 3. Check Input devices
if [ -d "/dev/input" ]; then
    bashio::log.info "Input Devices Found:"
    ls -la /dev/input
fi

bashio::log.info "===================================================="

bashio::log.info "Configuring Wayland runtime environment..."

export XDG_RUNTIME_DIR=/tmp/xdg
mkdir -p $XDG_RUNTIME_DIR
chmod 0700 $XDG_RUNTIME_DIR

# Start seatd without VT binding using its native environment variable
bashio::log.info "Starting seat management daemon..."
export SEATD_SOCK=/run/seatd.sock
export LIBSEAT_BACKEND=seatd
export SEATD_VTBOUND=0  # Tells seatd not to look for a physical TTY/VT

# Run seatd cleanly without invalid arguments
seatd -g root &

# Wait up to 3 seconds for the socket to actually generate
for i in $(seq 1 10); do
    if [ -S "$SEATD_SOCK" ]; then
        bashio::log.info "Seatd socket successfully established."
        break
    fi
    sleep 0.3
done

if [ ! -S "$SEATD_SOCK" ]; then
    bashio::log.error "CRITICAL: seatd failed to create socket!"
fi


# Clean residual display env vars
unset DISPLAY
unset WAYLAND_DISPLAY
export WLR_BACKEND=drm
# Earlier troubleshooting, we added export WLR_LIBINPUT_NO_DEVICES=1 to prevent wlroots from crashing if no devices were plugged in. 
# However, this flag completely disables libinput—meaning it is intentionally ignoring your touchscreen!
# export WLR_LIBINPUT_NO_DEVICES=1

# ---------------------------------------------------------
# READ UI OPTIONS (WITH BULLETPROOF FALLBACKS)
# ---------------------------------------------------------
URL=$(bashio::config 'ha_url')
ROTATION_CONFIG=$(bashio::config 'rotate_display')
IGNORE_CERTS=$(bashio::config 'ignore_certificate_errors')
SCREEN_TIMEOUT=$(bashio::config 'screen_timeout')

# If the HA API fails or fields are blank, FORCE these default values
if [ -z "$URL" ] || [ "$URL" == "null" ]; then
    URL="http://supervisor/core"
    bashio::log.warning "HA API returned blank URL. Forcing default."
fi

if [ -z "$SCREEN_TIMEOUT" ] || [ "$SCREEN_TIMEOUT" == "null" ]; then
    SCREEN_TIMEOUT=600
    bashio::log.warning "HA API returned blank timeout. Forcing 600s."
fi

if [ -z "$ROTATION_CONFIG" ] || [ "$ROTATION_CONFIG" == "null" ]; then
    ROTATION_CONFIG="normal"
fi
# ---------------------------------------------------------

# Map rotation values for wlr-randr
case $ROTATION_CONFIG in
    "right") ROTATION_DEGREES="90" ;;
    "inverted") ROTATION_DEGREES="180" ;;
    "left") ROTATION_DEGREES="270" ;;
    *) ROTATION_DEGREES="normal" ;;
esac

# Run rotation in the background
if [ "$ROTATION_DEGREES" != "normal" ]; then
    bashio::log.info "Scheduling screen rotation to ${ROTATION_DEGREES} degrees..."
    (
        sleep 5
        
        # 1. Dynamically find the Wayland socket Cage just created
        export WAYLAND_DISPLAY=$(ls /tmp/xdg | grep -m 1 "wayland-")
        
        # 2. Apply the rotation specifically to your DisplayPort hardware
        wlr-randr --output DP-1 --transform "$ROTATION_DEGREES"
    ) &
fi

# ... (The rest of your script starting Chromium remains the same) ...

# Build Chromium Ozone flags
CHROMIUM_FLAGS="--kiosk --no-sandbox --enable-features=UseOzonePlatform --ozone-platform=wayland --disable-infobars --remote-debugging-port=9222 --no-first-run --disable-sync --bwsi"

if bashio::config.true 'ignore_certificate_errors'; then
    CHROMIUM_FLAGS="${CHROMIUM_FLAGS} --ignore-certificate-errors"
fi

bashio::log.info "Starting background services..."

# Handle screen timeout
if [ "$SCREEN_TIMEOUT" -gt 0 ]; then
    bashio::log.info "Setting screen timeout to ${SCREEN_TIMEOUT} seconds..."
    swayidle -w \
        timeout "$SCREEN_TIMEOUT" 'wlr-randr --output \* --off' \
        resume 'wlr-randr --output \* --on' &
else
    bashio::log.info "Screen timeout disabled."
fi

# Start REST API server
python3 /app/rest_server.py &

bashio::log.info "Starting Cage with Chromium pointing to: ${URL}"

# Execute Cage and Chromium directly on DRM hardware
exec cage -s -- chromium-browser ${CHROMIUM_FLAGS} "${URL}"
