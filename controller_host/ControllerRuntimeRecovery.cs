using System.Diagnostics;
using HIDMaestro;
using Microsoft.Win32;

namespace VaporLockControllerHost;

// Exact durable creation inputs are the only authority for reconnecting a
// ParentPresent software device to change its lifetime and close it permanently.
internal static class ControllerRuntimeRecovery
{
    internal static void Reconcile(WindowsControllerComponentPlatform platform, ControllerComponentRecord record, bool retireRoot)
    {
        ControllerRuntimeOwnership.RefuseForeignNamespace(record);
        foreach (var creation in record.Creations.Where(item => !item.RemovalConfirmed).ToArray())
            RemoveCreation(platform, record, creation);
        foreach (var device in record.Devices.Where(item => !item.RemovalConfirmed))
            throw new IOException("A controller device has no completed owned creation recovery; it was preserved: " + device.InstanceId);
        foreach (var intent in record.Events.Where(item => item.Kind == "create-device" && item.State == "intent"
            && !record.Events.Any(done => done.Operation == item.Operation && done.State == "complete")).ToArray())
        {
            // Inputs must be journaled before Process.Start. A nonce without
            // inputs cannot have launched our helper. Still reject any matching
            // persistent node rather than adopting an inconsistent record.
            if (record.Creations.Any(item => item.Suffix == intent.Subject && !item.RemovalConfirmed)
                || ControllerRuntimeOwnership.DriverInstances().Any(id => id.EndsWith("\\" + intent.Subject, StringComparison.OrdinalIgnoreCase)))
                throw new IOException("Controller creation recovery is unresolved; configuration and trust were preserved.");
            record.Events.Add(new(intent.Operation, intent.Kind, intent.Subject, "complete")); platform.Save(record);
        }
        ControllerRuntimeOwnership.RetireOwnedConfiguration(platform, record, retireRoot);
    }

    internal static void RemoveCreation(WindowsControllerComponentPlatform platform, ControllerComponentRecord record, ControllerCreationEntry creation)
    {
        if (!record.Creations.Contains(creation) || !record.Events.Any(item => item.Kind == "create-device"
            && item.State == "intent" && item.Subject == creation.Suffix))
            throw new IOException("Controller removal has no durable creation authority.");
        if (ControllerStateQueries.Persistent(creation.InstanceId))
        {
            VerifyCreationIdentity(creation);
            var binding = ControllerRuntimeOwnership.DeviceBinding(creation.InstanceId, allowUnbound: true);
            var recorded = record.Devices.SingleOrDefault(item => item.InstanceId.Equals(creation.InstanceId, StringComparison.OrdinalIgnoreCase));
            ControllerRecoveryRules.ValidateBinding(record, creation, recorded, binding.Inf, binding.Service);
            if (binding.Inf.Length != 0)
                platform.VerifyPackage(record.Packages.Single(item => item.PublishedInf.Equals(binding.Inf, StringComparison.OrdinalIgnoreCase)), record);
            platform.HoldPayload(record);
            var start = new ProcessStartInfo(Path.Combine(record.PayloadDirectory, "hmswd.exe"))
            { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = record.PayloadDirectory,
                RedirectStandardOutput = true, RedirectStandardError = true };
            foreach (var value in new[] { "remove", creation.Enumerator, creation.Suffix, creation.Container.ToString("B"),
                string.Join('|', creation.HardwareIds), string.Join('|', creation.CompatibleIds), creation.Description })
                start.ArgumentList.Add(value);
            start.Environment.Clear(); start.Environment["SystemRoot"] = Path.GetDirectoryName(Environment.SystemDirectory)!;
            start.Environment["PATH"] = Environment.SystemDirectory;
            start.Environment["TEMP"] = record.PayloadDirectory; start.Environment["TMP"] = record.PayloadDirectory;
            using var process = Process.Start(start) ?? throw new IOException("Windows did not start the owned software-device removal helper.");
            using var job = new ControllerChildJob(process);
            var output = VaporLockIntegration.ReadBoundedHelperOutput(process.StandardOutput);
            var errors = VaporLockIntegration.ReadBoundedHelperOutput(process.StandardError);
            if (!process.WaitForExit(15000)) throw new IOException("Owned software-device lifetime removal timed out; cleanup remains pending.");
            output.GetAwaiter().GetResult(); errors.GetAwaiter().GetResult();
            if (process.ExitCode != 0) throw new IOException("Owned software-device lifetime removal failed; cleanup remains pending.");
            var elapsed = Stopwatch.StartNew();
            while (ControllerStateQueries.Persistent(creation.InstanceId) && elapsed.Elapsed < TimeSpan.FromSeconds(10)) Thread.Sleep(50);
            if (ControllerStateQueries.Persistent(creation.InstanceId))
                throw new IOException("The owned software-device state is still persistent. Cleanup remains pending; a restart may be required.");
        }
        record.Creations[record.Creations.IndexOf(creation)] = creation with { RemovalConfirmed = true };
        for (var index = 0; index < record.Devices.Count; index++)
            if (record.Devices[index].InstanceId.Equals(creation.InstanceId, StringComparison.OrdinalIgnoreCase))
                record.Devices[index] = record.Devices[index] with { RemovalConfirmed = true };
        platform.Save(record);
    }

    private static void VerifyCreationIdentity(ControllerCreationEntry creation)
    {
        using var node = Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Enum\" + creation.InstanceId)
            ?? throw new IOException("The pending controller's original Windows identity cannot be verified.");
        if (node.GetValue("HardwareID") is not string[] hardware || node.GetValue("CompatibleIDs") is not string[] compatible
            || !hardware.SequenceEqual(creation.HardwareIds, StringComparer.OrdinalIgnoreCase)
            || !compatible.SequenceEqual(creation.CompatibleIds, StringComparer.OrdinalIgnoreCase)
            || node.GetValue("ContainerID") is not string container || !Guid.TryParse(container, out var id) || id != creation.Container)
            throw new IOException("The pending software-device identity changed; it was preserved.");
    }
}
