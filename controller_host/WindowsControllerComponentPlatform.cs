using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Security.Principal;

namespace VaporLockControllerHost;

// Explicit elevated component transaction. Construction is never used by the
// driver-free test path or metadata/profile self-test.
internal sealed class WindowsControllerComponentPlatform : IControllerComponentPlatform
{
    internal readonly ControllerProtectedStorage Storage;
    private readonly Microsoft.Win32.SafeHandles.SafeFileHandle operation;
    private ControllerSigningIdentity? identity;
    private ControllerStagedPayload? staged;
    private readonly List<FileStream> payloadLeases = new();
    private ControllerComponentRecord? record;

    internal WindowsControllerComponentPlatform()
    {
        using var current = WindowsIdentity.GetCurrent();
        if (!new WindowsPrincipal(current).IsInRole(WindowsBuiltInRole.Administrator))
            throw new UnauthorizedAccessException("Administrator approval is required for the optional controller component.");
        operation = ControllerRuntimeOwnership.AcquireOperationReservation();
        try { Storage = new ControllerProtectedStorage(); }
        catch { operation.Dispose(); throw; }
    }

    public ControllerComponentRecord? Read()
    {
        record = Storage.ReadJournal<ControllerComponentRecord>("component.json");
        if (record is not null) ControllerComponentTransaction.Validate(record);
        return record;
    }

    public void Save(ControllerComponentRecord value)
    {
        ControllerComponentTransaction.Validate(value);
        if (record is not null && record.Component != value.Component)
        {
            if (record.Phase != "removed") throw new IOException("An active controller identity cannot be replaced.");
            var name = "removed-" + record.Component.ToString("N") + ".json";
            var archived = Storage.ReadJournal<ControllerComponentRecord>(name);
            if (archived is null) Storage.WriteJournal(name, record);
            else if (!System.Text.Json.JsonSerializer.SerializeToUtf8Bytes(archived).AsSpan()
                    .SequenceEqual(System.Text.Json.JsonSerializer.SerializeToUtf8Bytes(record)))
                throw new IOException("Existing controller identity archive differs; it was preserved.");
        }
        Storage.WriteJournal("component.json", value); record = value;
    }

    public void RefuseForeignState(ControllerComponentRecord? owned)
    {
        ControllerWindowsPackages.RefuseForeignPackages(owned);
        ControllerRuntimeOwnership.RefuseForeignNamespace(owned);
    }
    public void ReconcileOwnedRuntime(ControllerComponentRecord owned, bool retireConfiguration)
        => ControllerRuntimeRecovery.Reconcile(this, owned, retireConfiguration);

    public ControllerSignerProof PrepareSigner(ControllerComponentRecord value)
    {
        if (value.CertificateDer.Length == 0)
        {
            // A partially created, unrecorded key is not silently adopted or
            // rotated. The durable intent is retained for explicit recovery.
            if (CngKey.Exists(value.KeyName, new CngProvider(ControllerSigningIdentity.ProviderName), CngKeyOpenOptions.MachineKey))
                throw new CryptographicException("Controller key creation was interrupted. Its owned intent was preserved; repair requires explicit recovery.");
            identity = ControllerSigningIdentity.Create(value.Component, value.KeyName);
            value.CertificateDer = identity.Certificate.RawData;
            value.CertificateSHA256 = identity.CertificateSHA256; value.SpkiSHA256 = identity.SpkiSHA256;
            Save(value); // exact public identity BEFORE personal-store addition
        }
        else identity = ControllerSigningIdentity.Open(value.Component, value.KeyName,
            value.CertificateDer, value.CertificateSHA256, value.SpkiSHA256);
        RefuseDisallowed(value);
        using var store = new X509Store(StoreName.My, StoreLocation.LocalMachine); store.Open(OpenFlags.ReadWrite);
        var matches = store.Certificates.Cast<X509Certificate2>().Where(certificate =>
            certificate.Thumbprint.Equals(identity.Certificate.Thumbprint, StringComparison.OrdinalIgnoreCase)).ToArray();
        try
        {
            if (matches.Length > 1 || matches.Any(certificate => CertificateHash(certificate) != value.CertificateSHA256))
                throw new CryptographicException("Ambiguous controller signing certificate selection was rejected.");
            if (matches.Length == 0) store.Add(identity.Certificate);
            else
            {
                using var key = matches[0].GetRSAPrivateKey();
                if (key is not RSACng cng || cng.Key.KeyName != value.KeyName || !cng.Key.IsMachineKey
                    || cng.Key.Provider.Provider != ControllerSigningIdentity.ProviderName || cng.Key.ExportPolicy != CngExportPolicies.None)
                    throw new CryptographicException("Controller personal-store key association changed.");
            }
        }
        finally { foreach (var match in matches) match.Dispose(); }
        return new(value.CertificateDer, value.CertificateSHA256, value.SpkiSHA256);
    }

    public ControllerPayloadProof BuildAndVerifyPayload(ControllerComponentRecord value)
    {
        if (identity is null) throw new CryptographicException("The exact controller signer is not open.");
        if (value.PayloadHashes.Count == 6)
        {
            HoldPayload(value);
            return new(value.PayloadDirectory, value.PayloadHashes);
        }
        staged = new ControllerStagedPayload(Storage);
        return staged.Build(value, identity.Certificate, Save);
    }

    internal void HoldPayload(ControllerComponentRecord value)
    {
        if (value.PayloadHashes.Count != 6 || !value.PayloadDirectory.StartsWith(Storage.Root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
            || Path.GetDirectoryName(value.PayloadDirectory) != Storage.Root
            || !System.Text.RegularExpressions.Regex.IsMatch(Path.GetFileName(value.PayloadDirectory), @"\Apayload-[0-9a-f]{32}\z"))
            throw new IOException("Controller payload generation is not positively identified.");
        var files = new Dictionary<string, FileStream>(StringComparer.OrdinalIgnoreCase);
        try
        {
            foreach (var item in value.PayloadHashes)
            {
                if (!new[] { "hidmaestro.inf", "hidmaestro_xusb.inf", "HIDMaestro.dll", "HMXInput.dll", "hidmaestro.cat", "hidmaestro_xusb.cat" }.Contains(item.Key))
                    throw new IOException("Unexpected controller payload member.");
                var stream = Storage.HoldInput(Path.Combine(value.PayloadDirectory, item.Key)); files.Add(item.Key, stream);
                if (ControllerStagedPayload.Digest(stream) != item.Value) throw new CryptographicException("Controller payload bytes changed.");
            }
            var helper = Storage.HoldInput(Path.Combine(value.PayloadDirectory, "hmswd.exe")); files.Add("hmswd.exe", helper);
            if (ControllerStagedPayload.Digest(helper) != ControllerResources.Files.Single(item => item.FileName == "hmswd.exe").SHA256)
                throw new CryptographicException("The prepared controller helper changed.");
            if (!new HashSet<string>(Directory.EnumerateFiles(value.PayloadDirectory).Select(Path.GetFileName)!, StringComparer.OrdinalIgnoreCase).SetEquals(files.Keys)
                || Directory.EnumerateDirectories(value.PayloadDirectory).Any())
                throw new IOException("Unlisted prepared controller content was rejected.");
            ControllerSignatureVerifier.VerifyPackage(Path.Combine(value.PayloadDirectory, "hidmaestro.cat"),
                Path.Combine(value.PayloadDirectory, "HIDMaestro.dll"), files["HIDMaestro.dll"], files["hidmaestro.inf"], value.CertificateDer);
            ControllerSignatureVerifier.VerifyPackage(Path.Combine(value.PayloadDirectory, "hidmaestro_xusb.cat"),
                Path.Combine(value.PayloadDirectory, "HMXInput.dll"), files["HMXInput.dll"], files["hidmaestro_xusb.inf"], value.CertificateDer);
            payloadLeases.AddRange(files.Values); files.Clear();
        }
        finally { foreach (var stream in files.Values) stream.Dispose(); }
    }

    public bool TrustPresent(string name, ControllerComponentRecord value)
    {
        RefuseDisallowed(value);
        using var store = OpenPublicStore(name, OpenFlags.ReadOnly);
        var found = false;
        foreach (var certificate in store.Certificates)
        { using (certificate) if (CertificateHash(certificate) == value.CertificateSHA256) found = true; }
        return found;
    }
    public void AddTrust(string name, ControllerComponentRecord value)
    {
        RefuseDisallowed(value);
        if (identity is null || identity.CertificateSHA256 != value.CertificateSHA256)
            throw new CryptographicException("Only the recorded controller signer may be enrolled.");
        using var certificate = X509CertificateLoader.LoadCertificate(value.CertificateDer);
        using var store = OpenPublicStore(name, OpenFlags.ReadWrite); store.Add(certificate);
    }
    public ControllerPackageEntry ImportPackage(string originalInf, ControllerComponentRecord value)
        => ControllerWindowsPackages.Import(originalInf, value);
    public void VerifyPackage(ControllerPackageEntry package, ControllerComponentRecord value)
        => ControllerWindowsPackages.Verify(package, value);
    public void RemoveOwnedPackage(ControllerPackageEntry package, ControllerComponentRecord value)
        => ControllerWindowsPackages.Remove(package, value);
    public void RemoveOwnedDevice(ControllerDeviceEntry device, ControllerComponentRecord value)
    {
        var creation = value.Creations.SingleOrDefault(item => item.InstanceId.Equals(device.InstanceId, StringComparison.OrdinalIgnoreCase))
            ?? throw new IOException("The owned device has no recorded software-device creation inputs.");
        ControllerRuntimeRecovery.RemoveCreation(this, value, creation);
    }
    public bool CanRemoveTrust(ControllerComponentRecord value)
    {
        ControllerWindowsPackages.RefuseForeignPackages(value);
        return value.Packages.Count == 0 && value.Devices.All(device => device.RemovalConfirmed)
            && value.Creations.All(creation => creation.RemovalConfirmed)
            && !value.Events.Any(entry => entry.Kind == "create-device" && entry.State == "intent"
                && !value.Events.Any(done => done.Operation == entry.Operation && done.State == "complete"))
            && !value.Events.Any(entry => entry.Kind == "add-trust" && entry.State == "intent"
                && !value.Events.Any(done => done.Operation == entry.Operation && done.State == "complete"));
    }
    public void RemoveOwnedTrust(string name, ControllerComponentRecord value)
    {
        if (!value.Trust.Any(entry => entry.Store == name && entry.AddConfirmed && !entry.PriorPresent))
            throw new IOException("Preexisting or uncertain trust cannot be removed.");
        using var store = OpenPublicStore(name, OpenFlags.ReadWrite);
        foreach (var certificate in store.Certificates)
        { using (certificate) if (CertificateHash(certificate) == value.CertificateSHA256) store.Remove(certificate); }
    }
    public void RemoveOwnedKeyAndPersonalCertificate(ControllerComponentRecord value)
    {
        var exists = CngKey.Exists(value.KeyName, new CngProvider(ControllerSigningIdentity.ProviderName), CngKeyOpenOptions.MachineKey);
        if (!exists && !value.Events.Any(entry => entry.Kind == "remove-signer" && entry.Subject == value.KeyName && entry.State == "intent"))
            throw new CryptographicException("The controller signing key disappeared without an owned removal intent.");
        using var owned = exists ? ControllerSigningIdentity.Open(value.Component, value.KeyName,
            value.CertificateDer, value.CertificateSHA256, value.SpkiSHA256) : null;
        using var store = new X509Store(StoreName.My, StoreLocation.LocalMachine); store.Open(OpenFlags.ReadWrite);
        foreach (var certificate in store.Certificates)
        { using (certificate) if (CertificateHash(certificate) == value.CertificateSHA256) store.Remove(certificate); }
        if (exists)
        {
            using var key = CngKey.Open(value.KeyName, new CngProvider(ControllerSigningIdentity.ProviderName), CngKeyOpenOptions.MachineKey);
            key.Delete();
        }
    }
    private static X509Store OpenPublicStore(string name, OpenFlags access)
    {
        var storeName = name switch { "Root" => StoreName.Root, "TrustedPublisher" => StoreName.TrustedPublisher,
            _ => throw new IOException("Only the explicit controller trust stores are permitted.") };
        var store = new X509Store(storeName, StoreLocation.LocalMachine); store.Open(access); return store;
    }
    private static void RefuseDisallowed(ControllerComponentRecord value)
    {
        using var store = new X509Store(StoreName.Disallowed, StoreLocation.LocalMachine); store.Open(OpenFlags.ReadOnly);
        foreach (var certificate in store.Certificates)
        { using (certificate) if (CertificateHash(certificate) == value.CertificateSHA256) throw new CryptographicException("Windows explicitly disallows this controller signer; policy was preserved."); }
    }
    private static string CertificateHash(X509Certificate2 certificate) => Convert.ToHexStringLower(SHA256.HashData(certificate.RawData));
    public void Dispose()
    {
        foreach (var stream in payloadLeases) stream.Dispose(); payloadLeases.Clear();
        staged?.Dispose(); identity?.Dispose(); Storage.Dispose(); operation.Dispose();
    }
}
