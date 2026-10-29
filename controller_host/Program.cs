using System.Diagnostics;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text.Json;
using HIDMaestro;

namespace VaporLockControllerHost;

/// <summary>
/// Elevated, local-only companion for the optional Xbox One/Series bridge.
/// It owns the virtual HID device and accepts compact controller snapshots
/// from the native engine over a per-session named pipe. No network service,
/// device identity, or controller data leaves this computer.
/// </summary>
internal static class Program
{
    private static ControllerSessionStatus? sessionStatus;
    private const int ManualStickThreshold = 6000;

    private const byte PhysicalConnected = 1 << 0;
    private const byte ProxyMode = 1 << 1;
    private const byte LeftStickOutput = 1 << 2;
    private const byte TrackingActive = 1 << 3;
    private const byte ManualPriority = 1 << 4;

    private static readonly HashSet<string> CompatibleProfiles = new(StringComparer.OrdinalIgnoreCase)
    {
        "vapor-lock-xbox-one-windows",
        "xbox-one-original",
        "xbox-one-rev1",
        "xbox-one-s",
        "xbox-one-s-bt",
        "xbox-series-xs",
        "xbox-series-xs-bt",
    };

    private static async Task<int> Main(string[] args)
    {
        Options options;
        try
        {
            options = Options.Parse(args);
        }
        catch (ArgumentException error)
        {
            Console.Error.WriteLine(error.Message);
            return 2;
        }

        if (options.SelfTest)
            return SelfTest(options.Profile);

        if (string.IsNullOrWhiteSpace(options.StatusFile))
        {
            Console.Error.WriteLine("--status-file is required for a controller-host session.");
            return 2;
        }

        try
        {
            sessionStatus = new ControllerSessionStatus(options.StatusFile);
            return await RunAsync(options);
        }
        catch (Exception error)
        {
            try { WriteStatus(options, "failed", FriendlyError(error), null); } catch (IOException) { }
            Console.Error.WriteLine(error);
            return 1;
        }
        finally { sessionStatus?.Dispose(); sessionStatus = null; }
    }

    private static int SelfTest(string profileId)
    {
        try
        {
            VerifySessionPipeSecurity();
            VerifyVerticalAxisConvention();
            if (!CompatibleProfiles.Contains(profileId)) throw new ArgumentException("Unsupported controller profile.");
            var profile = VaporLockIntegration.EmbeddedProfile(profileId);
            VerifyNeutralFrame(profile);
            Console.WriteLine(JsonSerializer.Serialize(new
            {
                state = "self-test",
                message = "Verified the requested embedded controller profile without a driver context.",
                profile = profile.Id,
                device_name = profile.Name,
                compatible = true,
                pipe_access = "current-user",
                protocol_version = ControllerFrameReader.Version,
                neutralization = "verified",
            }));
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(FriendlyError(error));
            return 1;
        }
    }

    private static async Task<int> RunAsync(Options options)
    {
        if (!IsElevated())
            throw new UnauthorizedAccessException("Windows administrator approval is required to manage the Xbox One-compatible virtual controller.");

        using var platform = new WindowsControllerComponentPlatform();
        var transaction = new ControllerComponentTransaction(platform);
        if (options.Uninstall)
        {
            WriteStatus(options, "removing", "Removing Vapor Lock's local Xbox-compatible controller component.", null);
            transaction.Remove();
            WriteStatus(options, "uninstalled", "VaporLock-managed controller support was removed. Foreign and preexisting components were preserved.", null);
            return 0;
        }
        if (options.InstallOnly)
        {
            WriteStatus(options, "installing", "Installing or repairing explicitly approved VaporLock-managed controller support.", null);
            transaction.Install(options.MachineTrustConsent);
            WriteStatus(options, "installed", "Both owned controller packages are registered. Start a session to verify the virtual device.", null, options.Profile);
            return 0;
        }

        // Starting never creates a key, grants trust or installs packages.
        WriteStatus(options, "starting", "Opening the prepared VaporLock-managed controller component.", null);
        var record = platform.Read() ?? throw new IOException("Install or repair controller support in Movement before starting it.");
        using var ownership = new ControllerRuntimeOwnership(platform, record);
        VaporLockIntegration.Configure(ownership);
        using var context = new HMContext();
        LoadProfiles(context);
        var profile = ResolveProfile(context, options.Profile);

        var slotsBefore = CaptureXInputSlots();
        WriteStatus(options, "creating", $"Creating {profile.Name}.", null, profile.Id);
        var controller = context.CreateController(profile);
        try
        {
        var virtualSlot = await FindNewXInputSlotAsync(slotsBefore, TimeSpan.FromSeconds(2));

        // The host is elevated to create the Windows virtual HID device while
        // the inference engine intentionally remains unelevated. Grant only
        // the current user's SID access to this random per-session pipe so
        // Windows' split admin token can submit controller state locally.
        using var pipe = NamedPipeServerStreamAcl.Create(
            options.PipeName!,
            PipeDirection.In,
            1,
            PipeTransmissionMode.Byte,
            PipeOptions.Asynchronous,
            0,
            0,
            CreateSessionPipeSecurity(),
            HandleInheritability.None,
            (PipeAccessRights)0);

        var slotMessage = virtualSlot is int slot
            ? $"Xbox-compatible bridge ready in Controller {slot + 1}."
            : "Xbox-compatible bridge ready through Windows HID/GameInput.";
        WriteStatus(options, "ready", slotMessage, virtualSlot, profile.Id);

        using var stop = CancellationTokenSource.CreateLinkedTokenSource(ownership.ConflictToken);
        var stopWatcher = WatchStopFileAsync(options.StopFile, stop);
        try
        {
            await pipe.WaitForConnectionAsync(stop.Token);
            await ConsumeFramesAsync(pipe, controller, profile, stop.Token, ownership.AssertInputsHeld);
        }
        catch (OperationCanceledException) when (stop.IsCancellationRequested)
        {
            ownership.AssertInputsHeld(); // a foreign conflict is a failure, not a successful Stop
        }
        finally
        {
            stop.Cancel();
            try { await stopWatcher; } catch (OperationCanceledException) { }
        }
        }
        finally
        {
            try { controller.Dispose(); }
            finally { ownership.AssertRemoved(); }
        }
        WriteStatus(options, "stopped", "The controller stopped and its owned virtual device was removed.", null, profile.Id);
        return 0;
    }

    private static HMProfile ResolveProfile(HMContext context, string profileId)
    {
        if (!CompatibleProfiles.Contains(profileId))
            throw new ArgumentException("Choose one of Vapor Lock's supported Xbox One or Xbox Series controller identities.");
        var result = context.GetProfile(profileId)
            ?? throw new InvalidOperationException($"The required controller profile '{profileId}' is unavailable.");
        if (result.DriverMode != "xinputhid" || result.RequiresUsbipBackend)
            throw new InvalidOperationException("This bridge requires the embedded Xbox One/Series Windows profile.");
        return result;
    }

    private static int LoadProfiles(HMContext context)
    {
        return context.LoadDefaultProfiles();
    }

    private static async Task ConsumeFramesAsync(Stream stream, HMController controller, HMProfile profile, CancellationToken token, Action assertInputs)
    {
        var axes = HMGamepadStateHelpers.StandardAxes(profile);
        var state = new HMGamepadState { Axes = axes };
        void Submit(ControllerFrame frame)
        {
            assertInputs();
            ApplyFrame(ref state, axes, frame);
            controller.SubmitState(in state);
        }
        await ControllerFrameReader.ConsumeAsync(stream, Submit, () => Submit(default), token);
    }

    private static void ApplyFrame(ref HMGamepadState state, Dictionary<HMAxis, float> axes, ControllerFrame frame)
    {
        var proxy = (frame.Flags & ProxyMode) != 0 && (frame.Flags & PhysicalConnected) != 0;
        var leftX = proxy ? NormalizeStick(frame.LeftX) : 0.5f;
        var leftY = proxy ? NormalizeVerticalStick(frame.LeftY) : 0.5f;
        var rightX = proxy ? NormalizeStick(frame.RightX) : 0.5f;
        var rightY = proxy ? NormalizeVerticalStick(frame.RightY) : 0.5f;
        var leftTrigger = proxy ? frame.LeftTrigger / 255.0f : 0.0f;
        var rightTrigger = proxy ? frame.RightTrigger / 255.0f : 0.0f;

        var trackingActive = (frame.Flags & TrackingActive) != 0;
        if (trackingActive)
        {
            var outputOnLeft = (frame.Flags & LeftStickOutput) != 0;
            var manualPriority = (frame.Flags & ManualPriority) != 0;
            var physicalX = outputOnLeft ? frame.LeftX : frame.RightX;
            var physicalY = outputOnLeft ? frame.LeftY : frame.RightY;
            var physicalStickActive = Math.Abs((int)physicalX) >= ManualStickThreshold
                                      || Math.Abs((int)physicalY) >= ManualStickThreshold;
            if (!manualPriority || !physicalStickActive)
            {
                if (outputOnLeft)
                {
                    leftX = NormalizeStick(frame.TrackingX);
                    leftY = NormalizeVerticalStick(frame.TrackingY);
                }
                else
                {
                    rightX = NormalizeStick(frame.TrackingX);
                    rightY = NormalizeVerticalStick(frame.TrackingY);
                }
            }
        }

        // The standard Xbox profiles always expose the conventional first two
        // sticks and triggers. Keeping the dictionary alive avoids any heap
        // allocation on the controller's live input path.
        axes[HMAxis.X] = leftX;
        axes[HMAxis.Y] = leftY;
        axes[HMAxis.Rx] = rightX;
        axes[HMAxis.Ry] = rightY;
        axes[HMAxis.Z] = leftTrigger;
        axes[HMAxis.Rz] = rightTrigger;
        state.Buttons = proxy ? MapButtons(frame.Buttons) : HMButton.None;
        state.Hat = proxy ? MapHat(frame.Buttons) : HMHat.None;
    }

    private static HMButton MapButtons(ushort buttons)
    {
        HMButton mapped = HMButton.None;
        if ((buttons & 0x1000) != 0) mapped |= HMButton.A;
        if ((buttons & 0x2000) != 0) mapped |= HMButton.B;
        if ((buttons & 0x4000) != 0) mapped |= HMButton.X;
        if ((buttons & 0x8000) != 0) mapped |= HMButton.Y;
        if ((buttons & 0x0100) != 0) mapped |= HMButton.LeftBumper;
        if ((buttons & 0x0200) != 0) mapped |= HMButton.RightBumper;
        if ((buttons & 0x0020) != 0) mapped |= HMButton.Back;
        if ((buttons & 0x0010) != 0) mapped |= HMButton.Start;
        if ((buttons & 0x0040) != 0) mapped |= HMButton.LeftStick;
        if ((buttons & 0x0080) != 0) mapped |= HMButton.RightStick;
        return mapped;
    }

    private static HMHat MapHat(ushort buttons)
    {
        var up = (buttons & 0x0001) != 0;
        var down = (buttons & 0x0002) != 0;
        var left = (buttons & 0x0004) != 0;
        var right = (buttons & 0x0008) != 0;
        if (up && right) return HMHat.NorthEast;
        if (down && right) return HMHat.SouthEast;
        if (down && left) return HMHat.SouthWest;
        if (up && left) return HMHat.NorthWest;
        if (up) return HMHat.North;
        if (right) return HMHat.East;
        if (down) return HMHat.South;
        if (left) return HMHat.West;
        return HMHat.None;
    }

    private static float NormalizeStick(short value) => Math.Clamp(0.5f + value / 65534.0f, 0.0f, 1.0f);

    // XInput reports positive Y as up, while the Xbox HID profile represents
    // an upward stick position at the low end of its normalized axis. Convert
    // at this boundary so physical passthrough, the Windows tester, and the
    // generated tracking vector agree on the same direction.
    private static float NormalizeVerticalStick(short value) => 1.0f - NormalizeStick(value);

    private static void VerifyVerticalAxisConvention()
    {
        var up = NormalizeVerticalStick(short.MaxValue);
        var neutral = NormalizeVerticalStick(0);
        var down = NormalizeVerticalStick(short.MinValue);
        if (!(up < neutral && neutral < down && up <= 0.001f && down >= 0.999f))
            throw new InvalidOperationException("The Xbox HID vertical-axis convention is invalid.");
    }

    private static void VerifyNeutralFrame(HMProfile profile)
    {
        var axes = HMGamepadStateHelpers.StandardAxes(profile);
        var state = new HMGamepadState { Axes = axes };
        ApplyFrame(ref state, axes, new ControllerFrame(0xffff, 255, 255,
            short.MinValue, short.MaxValue, short.MaxValue, short.MinValue, 200, -300,
            PhysicalConnected | ProxyMode | TrackingActive | ManualPriority));
        ApplyFrame(ref state, axes, default);
        if (state.Buttons != HMButton.None || state.Hat != HMHat.None
            || axes[HMAxis.X] != 0.5f || axes[HMAxis.Y] != 0.5f
            || axes[HMAxis.Rx] != 0.5f || axes[HMAxis.Ry] != 0.5f
            || axes[HMAxis.Z] != 0.0f || axes[HMAxis.Rz] != 0.0f)
            throw new InvalidOperationException("Controller neutralization did not release every mapped input.");
    }

    private static async Task WatchStopFileAsync(string? stopFile, CancellationTokenSource stop)
    {
        if (string.IsNullOrWhiteSpace(stopFile))
            return;
        while (!stop.IsCancellationRequested)
        {
            if (File.Exists(stopFile))
            {
                stop.Cancel();
                return;
            }
            await Task.Delay(100, stop.Token);
        }
    }

    private static bool[] CaptureXInputSlots()
    {
        var result = new bool[4];
        for (uint slot = 0; slot < result.Length; slot++)
            result[slot] = XInputGetState(slot, out _) == 0;
        return result;
    }

    private static async Task<int?> FindNewXInputSlotAsync(bool[] before, TimeSpan timeout)
    {
        var stopwatch = Stopwatch.StartNew();
        while (stopwatch.Elapsed < timeout)
        {
            for (uint slot = 0; slot < before.Length; slot++)
            {
                if (!before[slot] && XInputGetState(slot, out _) == 0)
                    return (int)slot;
            }
            await Task.Delay(20);
        }
        return null;
    }

    private static bool IsElevated()
    {
        using var identity = WindowsIdentity.GetCurrent();
        return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
    }

    private static PipeSecurity CreateSessionPipeSecurity()
    {
        using var identity = WindowsIdentity.GetCurrent();
        var user = identity.User
            ?? throw new UnauthorizedAccessException("Windows could not identify the local user for the controller bridge.");
        var security = new PipeSecurity();
        security.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
        security.SetOwner(user);
        security.AddAccessRule(new PipeAccessRule(user, PipeAccessRights.FullControl, AccessControlType.Allow));
        return security;
    }

    private static void VerifySessionPipeSecurity()
    {
        using var pipe = NamedPipeServerStreamAcl.Create(
            "VaporLockControllerAclCheck-" + Guid.NewGuid().ToString("N"),
            PipeDirection.In,
            1,
            PipeTransmissionMode.Byte,
            PipeOptions.Asynchronous,
            0,
            0,
            CreateSessionPipeSecurity(),
            HandleInheritability.None,
            (PipeAccessRights)0);
    }

    private static void WriteStatus(Options options, string state, string message, int? virtualSlot, string? profile = null)
    {
        var payload = JsonSerializer.Serialize(new
        {
            state,
            message,
            profile,
            virtual_slot = virtualSlot,
            bridge = "xbox-one-hid",
            protocol_version = ControllerFrameReader.Version,
        });
        Console.WriteLine(payload);
        Console.Out.Flush();
        if (string.IsNullOrWhiteSpace(options.StatusFile))
            return;
        sessionStatus?.Write(payload);
    }

    private static string FriendlyError(Exception error) => error switch
    {
        UnauthorizedAccessException => "Windows administrator approval is required for the Xbox One-compatible controller bridge.",
        ArgumentException => error.Message,
        _ => "The Xbox One-compatible controller bridge could not start: " + error.Message,
    };

    [DllImport("xinput1_4.dll", EntryPoint = "XInputGetState")]
    private static extern uint XInputGetState(uint userIndex, out XInputState state);

    [StructLayout(LayoutKind.Sequential)]
    private struct XInputState
    {
        public uint PacketNumber;
        public XInputGamepad Gamepad;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct XInputGamepad
    {
        public ushort Buttons;
        public byte LeftTrigger;
        public byte RightTrigger;
        public short LeftX;
        public short LeftY;
        public short RightX;
        public short RightY;
    }

    private sealed class Options
    {
        public string? PipeName { get; private init; }
        public string Profile { get; private init; } = "vapor-lock-xbox-one-windows";
        public string? StatusFile { get; private init; }
        public string? StopFile { get; private init; }
        public bool InstallOnly { get; private init; }
        public bool MachineTrustConsent { get; private init; }
        public bool Uninstall { get; private init; }
        public bool SelfTest { get; private init; }

        public static Options Parse(string[] args)
        {
            string? pipe = null;
            string? status = null;
            string? stop = null;
            var profile = "vapor-lock-xbox-one-windows";
            var installOnly = false;
            var trustConsent = false;
            var uninstall = false;
            var selfTest = false;
            for (var index = 0; index < args.Length; index++)
            {
                var argument = args[index];
                string Next()
                {
                    if (++index >= args.Length || string.IsNullOrWhiteSpace(args[index]))
                        throw new ArgumentException($"{argument} requires a value.");
                    return args[index];
                }
                switch (argument)
                {
                    case "--pipe": pipe = Next(); break;
                    case "--profile": profile = Next(); break;
                    case "--status-file": status = Next(); break;
                    case "--stop-file": stop = Next(); break;
                    case "--install-only": installOnly = true; break;
                    case "--machine-trust-consent": trustConsent = true; break;
                    case "--uninstall": uninstall = true; break;
                    case "--self-test": selfTest = true; break;
                    default: throw new ArgumentException($"Unknown option: {argument}");
                }
            }
            if (!selfTest && !installOnly && !uninstall && string.IsNullOrWhiteSpace(pipe))
                throw new ArgumentException("--pipe is required for a controller-host session.");
            if ((installOnly ? 1 : 0) + (uninstall ? 1 : 0) + (selfTest ? 1 : 0) > 1 || trustConsent != installOnly)
                throw new ArgumentException("Choose one operation; installation requires explicit --machine-trust-consent.");
            if (!CompatibleProfiles.Contains(profile)) throw new ArgumentException("Unsupported controller profile.");
            if (!selfTest && !installOnly && !uninstall && (!System.Text.RegularExpressions.Regex.IsMatch(pipe!, @"\AVaporLockController-[0-9a-f]{32}\z")
                || Path.GetFileName(stop) != "session-" + pipe![20..] + ".stop"
                || !Path.GetDirectoryName(Path.GetFullPath(stop!))!.Equals(Path.GetDirectoryName(Path.GetFullPath(status!)), StringComparison.OrdinalIgnoreCase)))
                throw new ArgumentException("Controller session identifiers do not match their generated operation.");
            return new Options
            {
                PipeName = pipe,
                Profile = profile,
                StatusFile = status,
                StopFile = stop,
                InstallOnly = installOnly,
                MachineTrustConsent = trustConsent,
                Uninstall = uninstall,
                SelfTest = selfTest,
            };
        }
    }
}
