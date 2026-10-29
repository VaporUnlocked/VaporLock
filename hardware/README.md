# Vapor Lock hardware bridge

`vapor_lock_hid_bridge/VaporLockHidBridge.ino` is the movement-only firmware
used by the **Vapor Lock HID bridge** transport. It accepts relative movement
over its USB serial port and emits it through the board's standard USB mouse
interface.

Supported board families:

- Arduino Micro / Leonardo-class ATmega32U4 boards
- RP2350 boards using an HID-capable Arduino core
- Teensy 4.1 configured with a USB Mouse-capable USB type

Select the board's COM port and baud rate in Output > Hardware after flashing
the firmware. The protocol intentionally contains no keyboard, click,
remapping, device-identity, masking, or input-blocking controls.

## Optional virtual mouse transport

`interception/` contains the administrator-installed driver component used by
the **Virtual mouse - driver-level input** transport. It is disabled by default.
Installing it requires Windows administrator approval and a restart before the
transport can be used. The component accepts relative movement only.
