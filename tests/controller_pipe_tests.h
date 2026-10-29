// Real local pipes with recording readers; no controller host or device.
static void runControllerPipeTests() {
    {
        const auto healthyName = L"VaporLock-test-healthy-" + std::to_wstring(GetCurrentProcessId());
        const auto healthyPath = L"\\\\.\\pipe\\" + healthyName;
        ScopedWinHandle reader{CreateNamedPipeW(healthyPath.c_str(), PIPE_ACCESS_INBOUND,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
            1, 0, 4096, 0, nullptr)};
        require(reader.value != INVALID_HANDLE_VALUE, "healthy controller pipe fixture failed");
        ControllerHostOutput writer(healthyName);
        ControllerSnapshot input;
        input.connected = true;
        input.gamepad.buttons = 0x1234;
        input.gamepad.leftTrigger = 7; input.gamepad.rightTrigger = 99;
        input.gamepad.thumbLX = -32768; input.gamepad.thumbLY = 32767;
        input.gamepad.thumbRX = -456; input.gamepad.thumbRY = 789;
        const auto before = GetTickCount64();
        require(writer.send(input, -200, 300, true, true, true, true), "healthy controller frame rejected");
        ControllerHostFrame frame{};
        DWORD received = 0;
        require(ReadFile(reader.value, &frame, sizeof(frame), &received, nullptr) && received == sizeof(frame),
            "healthy controller frame incomplete");
        require(frame.magic == 0x48434C56 && frame.version == 2 && sizeof(frame) == 32
            && offsetof(ControllerHostFrame, sentAtMilliseconds) == 24 && frame.reserved == 0 && frame.flags == 31,
            "controller wire header or timestamp layout changed");
        require(frame.buttons == 0x1234 && frame.leftTrigger == 7 && frame.rightTrigger == 99
            && frame.leftX == -32768 && frame.leftY == 32767 && frame.rightX == -456 && frame.rightY == 789
            && frame.trackingX == -200 && frame.trackingY == 300,
            "controller wire lost passthrough or assistance values");
        require(frame.sentAtMilliseconds >= before && frame.sentAtMilliseconds <= GetTickCount64(),
            "controller frame timestamp is not the shared Windows uptime clock");
        require(writer.send({}, 0, 0, true, false, false, true), "neutral controller frame rejected");
        require(ReadFile(reader.value, &frame, sizeof(frame), &received, nullptr) && received == sizeof(frame)
            && frame.buttons == 0 && frame.leftTrigger == 0 && frame.rightTrigger == 0 && frame.leftX == 0
            && frame.rightY == 0 && frame.trackingX == 0 && frame.trackingY == 0 && frame.flags == 18,
            "disconnected controller retained old physical state");
    }
    const auto name = L"VaporLock-test-stalled-" + std::to_wstring(GetCurrentProcessId())
        + L"-" + std::to_wstring(GetTickCount64());
    const auto path = L"\\\\.\\pipe\\" + name;
    const HANDLE server = CreateNamedPipeW(path.c_str(), PIPE_ACCESS_INBOUND | FILE_FLAG_OVERLAPPED,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
        1, 0, 32, 0, nullptr);
    require(server != INVALID_HANDLE_VALUE, "could not create the controller pipe test fixture");
    // A rescue closes only this test server so the old blocking implementation
    // fails an assertion rather than hanging the test runner indefinitely.
    std::jthread rescue([server] { Sleep(600); CloseHandle(server); });
    ControllerHostOutput output(name);
    ControllerSnapshot physical;
    double longestMilliseconds = 0;
    bool rejected = false;
    for (int attempt = 0; attempt < 4096; ++attempt) {
        const auto before = AimClock::now();
        const bool accepted = output.send(physical, 100, -100, false, false, true, true);
        longestMilliseconds = std::max(longestMilliseconds,
            std::chrono::duration<double, std::milli>(AimClock::now() - before).count());
        if (!accepted) { rejected = true; break; }
    }
    require(rejected, "unread controller pipe did not reject backpressure");
    std::cout << "Controller stalled-pipe maximum write wait: " << longestMilliseconds << " ms\n";
    require(longestMilliseconds < 200, "controller pipe blocked output/cancellation on an unread receiver");
    require(output.unavailableTooLong() && !output.connected() && !output.lastError().empty(),
        "failed controller stream was not marked terminal");
    require(!output.send({}, 300, 400, false, false, true, false), "failed controller stream silently rearmed");

    AppConfig settings;
    settings.movementEnabled = true;
    settings.movementSmoothing = 0;
    int movements = 0, neutralReports = 0, releasedEvents = 0;
    AimOutputScheduler scheduler([&](const AimReport& report, const AimInputs&, const AppConfig&, AimOutputStatus& status) {
        if (!report.active) { ++neutralReports; return true; }
        ++movements;
        status.fatalError = L"test fatal transport failure";
        return false;
    }, [](const AppConfig&) { AimInputs inputs; inputs.down = true; return inputs; },
    [&](const AppConfig&, bool authorized, bool, AimTime, AimOutputStatus&) { if (!authorized) ++releasedEvents; });
    const auto generation = scheduler.configure(settings);
    releasedEvents = 0;
    scheduler.publish(timedSubject(1, 400, aimTime(0)), 640, 640, aimTime(0), generation);
    scheduler.pollOnce(aimTime(0));
    require(movements == 1 && neutralReports == 1 && releasedEvents == 1 && !scheduler.status().active,
        "fatal output failure did not immediately cancel movement/events");
    scheduler.publish(timedSubject(1, 400, aimTime(.1)), 640, 640, aimTime(.1), generation);
    scheduler.pollOnce(aimTime(.1));
    require(movements == 1 && !scheduler.status().fatalError.empty(), "fatal transport automatically rearmed");
}
