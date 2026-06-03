#!/bin/sh
# Start virtual display for headless Chromium
Xvfb :99 -screen 0 1280x1024x24 &
XVFB_PID=$!
export DISPLAY=:99

# Run the main command
echo "Starting Xvfb on display :99 (PID $XVFB_PID)"
"$@"
EXIT_CODE=$?

kill $XVFB_PID 2>/dev/null
exit $EXIT_CODE
