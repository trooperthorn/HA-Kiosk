#!/usr/bin/bashio

bashio::log.info "Configuring Wayland runtime environment..."

export XDG_RUNTIME_DIR=/tmp/xdg
mkdir -p $XDG_RUNTIME_DIR
chmod 0700 $XDG_RUNTIME_DIR
export WAYLAND_DISPLAY=wayland-0

# Read your specific UI options
URL=$(bashio::config 'ha_url')
ROTATION_CONFIG=$(bashio::config 'rotate_display')
IGNORE_CERTS=$(bashio::config 'ignore_certificate_errors')

# Map rotation values to Wayland transforms
case $ROTATION_CONFIG in
    "normal") export WLR_OUTPUT_TRANSFORM="normal" ;;
    "right") export WLR_OUTPUT_TRANSFORM="90" ;;
    "inverted") export WLR_OUTPUT_TRANSFORM="180" ;;
    "left") export WLR_OUTPUT_TRANSFORM="270" ;;
    *) export WLR_OUTPUT_TRANSFORM="normal" ;;
esac

# Build Chromium Ozone flags
CHROMIUM_FLAGS="--kiosk --no-sandbox --enable-features=UseOzonePlatform --ozone-platform=wayland --disable-infobars --remote-debugging-port=9222"

if bashio::config.true 'ignore_certificate_errors'; then
    CHROMIUM_FLAGS="${CHROMIUM_FLAGS} --ignore-certificate-errors"
fi

bashio::log.info "Starting background services..."

# Start the screen timeout daemon in the background (Default 600s / 10m)
swayidle -w \
    timeout 600 'wlr-randr --output \* --off' \
    resume 'wlr-randr --output \* --on' &

# Start the REST API server in the background
python3 /app/rest_server.py &

bashio::log.info "Starting Cage with Chromium pointing to: ${URL}"

# Execute Cage and Chromium (This MUST be the final line)
exec cage -s -- chromium-browser ${CHROMIUM_FLAGS} "${URL}"
