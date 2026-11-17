#pragma once

#include "VideoInput.h"
#include <memory>

namespace vaporlock::video {

class WindowInput {
public:
    WindowInput();
    ~WindowInput();
    bool open(HWND window, DWORD pid);
    Input::Result poll(Crop crop, Frame& output);

private:
    struct State;
    std::unique_ptr<State> state_;
};

}
