// Vapor Lock HID Bridge protocol 1
//
// Compatible with Arduino Micro/Leonardo-class ATmega32U4 boards, RP2350
// boards using an Arduino HID-capable core, and Teensy 4.1 configured with a
// USB Mouse-capable USB type. It accepts only relative movement frames:
//
//     VLMB 1 <x> <y>\n
//
// No keyboard, button, input-remapping, identity, or blocking commands exist
// in this firmware.

#include <Mouse.h>

namespace {

constexpr long kMaximumMovement = 32767;
constexpr size_t kMaximumLineLength = 48;
char line[kMaximumLineLength]{};
size_t lineLength = 0;

long bounded(long value) {
  if (value > kMaximumMovement) return kMaximumMovement;
  if (value < -kMaximumMovement) return -kMaximumMovement;
  return value;
}

void moveRelative(long x, long y) {
  x = bounded(x);
  y = bounded(y);
  while (x != 0 || y != 0) {
    const int stepX = constrain(x, -127, 127);
    const int stepY = constrain(y, -127, 127);
    Mouse.move(stepX, stepY, 0);
    x -= stepX;
    y -= stepY;
  }
}

void processLine() {
  long version = 0;
  long x = 0;
  long y = 0;
  if (sscanf(line, "VLMB %ld %ld %ld", &version, &x, &y) == 3 && version == 1) {
    moveRelative(x, y);
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  Mouse.begin();
}

void loop() {
  while (Serial.available() > 0) {
    const char value = static_cast<char>(Serial.read());
    if (value == '\n') {
      line[lineLength] = '\0';
      processLine();
      lineLength = 0;
      continue;
    }
    if (value != '\r' && lineLength + 1 < kMaximumLineLength) {
      line[lineLength++] = value;
    } else if (lineLength + 1 >= kMaximumLineLength) {
      lineLength = 0;
    }
  }
}
