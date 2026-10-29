using System.Security.Cryptography;

namespace VaporLockControllerHost;

internal sealed class ControllerComponentRecord
{
    public int Schema { get; set; } = 1;
    public Guid Component { get; set; }
    public string KeyName { get; set; } = "";
    public string ResourceAssemblySHA256 { get; set; } = "";
    public string Phase { get; set; } = "identity-intent";
    public byte[] CertificateDer { get; set; } = Array.Empty<byte>();
    public string CertificateSHA256 { get; set; } = "";
    public string SpkiSHA256 { get; set; } = "";
    public string PayloadDirectory { get; set; } = "";
    public Dictionary<string, string> PayloadHashes { get; set; } = new(StringComparer.OrdinalIgnoreCase);
    public List<ControllerTrustEntry> Trust { get; set; } = new();
    public List<ControllerPackageEntry> Packages { get; set; } = new();
    public List<ControllerDeviceEntry> Devices { get; set; } = new();
    public List<ControllerCreationEntry> Creations { get; set; } = new();
    public List<ControllerTransactionEvent> Events { get; set; } = new();
}

internal sealed record ControllerTransactionEvent(Guid Operation, string Kind, string Subject, string State);
internal sealed record ControllerTrustEntry(string Store, bool PriorPresent, bool AddConfirmed);
internal sealed record ControllerPackageEntry(string OriginalInf, string PublishedInf, string StoreDirectory,
    string InfSHA256, string DriverSHA256, string CatalogSHA256, bool ImportConfirmed, bool PriorPresent);
internal sealed record ControllerDeviceEntry(string InstanceId, int Index, string InfName, string Service,
    Guid Session, bool RemovalConfirmed = false);
internal sealed record ControllerCreationEntry(string Suffix, string Enumerator, string[] HardwareIds,
    string[] CompatibleIds, Guid Container, string Description, Guid Session, bool RemovalConfirmed = false)
{
    public string InstanceId => @"SWD\" + Enumerator + "\\" + Suffix;
}
internal sealed record ControllerSignerProof(byte[] Der, string DerSHA256, string SpkiSHA256);
internal sealed record ControllerPayloadProof(string Directory, Dictionary<string, string> Files);

// Native side effects are behind this boundary. Tests supply an in-memory
// platform; merely constructing the transaction never creates a key or driver.
internal interface IControllerComponentPlatform : IDisposable
{
    ControllerComponentRecord? Read();
    void Save(ControllerComponentRecord record);
    void RefuseForeignState(ControllerComponentRecord? owned);
    void ReconcileOwnedRuntime(ControllerComponentRecord owned, bool retireConfiguration);
    ControllerSignerProof PrepareSigner(ControllerComponentRecord record);
    ControllerPayloadProof BuildAndVerifyPayload(ControllerComponentRecord record);
    bool TrustPresent(string store, ControllerComponentRecord record);
    void AddTrust(string store, ControllerComponentRecord record);
    ControllerPackageEntry ImportPackage(string originalInf, ControllerComponentRecord record);
    void VerifyPackage(ControllerPackageEntry package, ControllerComponentRecord record);
    void RemoveOwnedDevice(ControllerDeviceEntry device, ControllerComponentRecord record);
    void RemoveOwnedPackage(ControllerPackageEntry package, ControllerComponentRecord record);
    bool CanRemoveTrust(ControllerComponentRecord record);
    void RemoveOwnedTrust(string store, ControllerComponentRecord record);
    void RemoveOwnedKeyAndPersonalCertificate(ControllerComponentRecord record);
}

internal sealed class ControllerComponentTransaction
{
    internal const string ResourceSourceSHA256 = "95c6d3ec0d41cd7b3adda80da3ffbcd19c9466cc50e08a973c7745a16c7e0bde";
    private static readonly string[] InfNames = { "hidmaestro.inf", "hidmaestro_xusb.inf" };
    private readonly IControllerComponentPlatform platform;
    internal ControllerComponentTransaction(IControllerComponentPlatform implementation) => platform = implementation;

    internal ControllerComponentRecord Install(bool machineTrustConsent)
    {
        if (!machineTrustConsent) throw new InvalidOperationException("Controller setup needs explicit machine-trust consent.");
        var record = platform.Read();
        if (record is not null) Validate(record);
        if (record is not null) platform.ReconcileOwnedRuntime(record, retireConfiguration: record.Phase == "removed");
        // No provenance record permits adopting a foreign same-subject signer,
        // matching shared namespace or package. Conflict detection precedes keys.
        platform.RefuseForeignState(record);
        if (record?.Phase == "installed")
        {
            if (record.Packages.Count != 2) throw new IOException("Owned controller package record is incomplete.");
            foreach (var package in record.Packages) platform.VerifyPackage(package, record);
            if (platform.TrustPresent("Root", record) && platform.TrustPresent("TrustedPublisher", record)) return record;
            record.Phase = "repair"; platform.Save(record);
        }
        if (record?.Phase == "removed") record = null; // old identity is archived by the protected platform before replacement
        if (record is null)
        {
            var id = Guid.NewGuid();
            record = new ControllerComponentRecord { Component = id,
                KeyName = "VaporLock.Controller." + id.ToString("N"), ResourceAssemblySHA256 = ResourceSourceSHA256 };
            platform.Save(record); // durable identity intent BEFORE key creation
        }
        var signerOperation = Intent(record, "prepare-signer", record.KeyName);
        var signer = platform.PrepareSigner(record);
        if (Convert.ToHexStringLower(SHA256.HashData(signer.Der)) != signer.DerSHA256
            || !Digest(signer.DerSHA256) || !Digest(signer.SpkiSHA256))
            throw new CryptographicException("Controller signer proof is inconsistent.");
        if (record.CertificateDer.Length != 0 && (record.CertificateSHA256 != signer.DerSHA256 || record.SpkiSHA256 != signer.SpkiSHA256))
            throw new CryptographicException("An existing controller identity cannot be silently rotated.");
        record.CertificateDer = signer.Der; record.CertificateSHA256 = signer.DerSHA256; record.SpkiSHA256 = signer.SpkiSHA256;
        Complete(record, signerOperation, "prepare-signer", record.KeyName, "signer-recorded");
        var payloadOperation = Intent(record, "prepare-payload", "both driver packages");
        var payload = platform.BuildAndVerifyPayload(record);
        if (payload.Files.Count != 6 || payload.Files.Any(entry => !Digest(entry.Value))
            || !new HashSet<string>(payload.Files.Keys, StringComparer.OrdinalIgnoreCase).SetEquals(new[] {
                "hidmaestro.inf", "hidmaestro_xusb.inf", "HIDMaestro.dll", "HMXInput.dll", "hidmaestro.cat", "hidmaestro_xusb.cat" }))
            throw new CryptographicException("Controller payload proof is incomplete.");
        record.PayloadDirectory = payload.Directory; record.PayloadHashes = payload.Files;
        Complete(record, payloadOperation, "prepare-payload", "both driver packages", "signatures-verified");
        // Every signature/member binding was verified before either trust add.
        foreach (var store in new[] { "Root", "TrustedPublisher" })
        {
            var entry = record.Trust.SingleOrDefault(value => value.Store == store);
            if (entry is null)
            {
                entry = new(store, platform.TrustPresent(store, record), false);
                record.Trust.Add(entry); platform.Save(record);
            }
            if (!platform.TrustPresent(store, record))
            {
                var operation = Intent(record, "add-trust", store);
                platform.AddTrust(store, record);
                if (!platform.TrustPresent(store, record)) throw new IOException("Windows did not retain controller trust.");
                record.Trust[record.Trust.IndexOf(entry)] = entry with { AddConfirmed = true };
                Complete(record, operation, "add-trust", store, "trust-added");
            }
        }
        foreach (var originalInf in InfNames)
        {
            var package = record.Packages.SingleOrDefault(value => value.OriginalInf == originalInf);
            if (package is null)
            {
                var operation = Intent(record, "import-package", originalInf);
                package = platform.ImportPackage(originalInf, record);
                if (package.OriginalInf != originalInf || package.PriorPresent || !package.ImportConfirmed
                    || record.Packages.Any(value => value.PublishedInf.Equals(package.PublishedInf, StringComparison.OrdinalIgnoreCase)))
                    throw new IOException("Windows package identity is ambiguous or pre-existing.");
                record.Packages.Add(package);
                Complete(record, operation, "import-package", originalInf, "package-imported");
            }
            platform.VerifyPackage(package, record);
        }
        record.Phase = "installed"; platform.Save(record);
        return record; // package registration, NOT an operational device claim
    }

    internal void Remove()
    {
        var record = platform.Read() ?? throw new IOException("No managed controller provenance exists; unknown components were preserved.");
        Validate(record);
        if (record.Phase == "removed") return;
        platform.ReconcileOwnedRuntime(record, retireConfiguration: true);
        foreach (var device in record.Devices.Where(value => !value.RemovalConfirmed).ToArray())
        {
            var operation = Intent(record, "remove-device", device.InstanceId);
            platform.RemoveOwnedDevice(device, record);
            record.Devices[record.Devices.IndexOf(device)] = device with { RemovalConfirmed = true };
            Complete(record, operation, "remove-device", device.InstanceId, "removing");
        }
        foreach (var package in record.Packages.ToArray())
        {
            // Presence, exact recorded bytes/signer and ownership are rechecked
            // by the platform before deletion, never vendor-name matching.
            var operation = Intent(record, "remove-package", package.PublishedInf);
            platform.RemoveOwnedPackage(package, record);
            record.Packages.Remove(package);
            Complete(record, operation, "remove-package", package.PublishedInf, "removing");
        }
        if (!platform.CanRemoveTrust(record))
            throw new IOException("Shared or uncertain controller trust was preserved; removal is incomplete.");
        foreach (var entry in record.Trust.Where(value => value.AddConfirmed && !value.PriorPresent).ToArray())
        {
            var operation = Intent(record, "remove-trust", entry.Store);
            platform.RemoveOwnedTrust(entry.Store, record);
            record.Trust.Remove(entry);
            Complete(record, operation, "remove-trust", entry.Store, "removing");
        }
        var keyOperation = Intent(record, "remove-signer", record.KeyName);
        platform.RemoveOwnedKeyAndPersonalCertificate(record);
        Complete(record, keyOperation, "remove-signer", record.KeyName, "removed");
    }

    private Guid Intent(ControllerComponentRecord record, string kind, string subject)
    {
        if (record.Events.Count > 1022) throw new IOException("Controller journal needs room for both intent and completion.");
        var id = Guid.NewGuid(); record.Events.Add(new(id, kind, subject, "intent")); platform.Save(record); return id;
    }
    private void Complete(ControllerComponentRecord record, Guid operation, string kind, string subject, string phase)
    {
        record.Events.Add(new(operation, kind, subject, "complete")); record.Phase = phase; platform.Save(record);
    }
    internal static void Validate(ControllerComponentRecord record)
    {
        if (record.Schema != 1 || record.Component == Guid.Empty || record.KeyName != "VaporLock.Controller." + record.Component.ToString("N")
            || record.ResourceAssemblySHA256 != ResourceSourceSHA256 || record.Events.Count > 1024
            || record.Trust.Any(value => value.Store is not ("Root" or "TrustedPublisher"))
            || record.Trust.Select(value => value.Store).Distinct().Count() != record.Trust.Count
            || record.Packages.Count > 2 || record.Packages.Any(value => !InfNames.Contains(value.OriginalInf)
                || !System.Text.RegularExpressions.Regex.IsMatch(value.PublishedInf, @"\Aoem[0-9]+\.inf\z", System.Text.RegularExpressions.RegexOptions.IgnoreCase | System.Text.RegularExpressions.RegexOptions.CultureInvariant))
            || record.Packages.Select(value => value.OriginalInf).Distinct(StringComparer.OrdinalIgnoreCase).Count() != record.Packages.Count
            || record.Packages.Select(value => value.PublishedInf).Distinct(StringComparer.OrdinalIgnoreCase).Count() != record.Packages.Count
            || record.Phase == "installed" && !new HashSet<string>(record.Packages.Select(value => value.OriginalInf), StringComparer.OrdinalIgnoreCase).SetEquals(InfNames))
            throw new IOException("Controller installation provenance is invalid or unsupported.");
        if (record.CertificateDer.Length != 0 && (!Digest(record.CertificateSHA256) || !Digest(record.SpkiSHA256)
            || Convert.ToHexStringLower(SHA256.HashData(record.CertificateDer)) != record.CertificateSHA256))
            throw new CryptographicException("Controller signer provenance is corrupt.");
    }
    private static bool Digest(string value) => value.Length == 64 && value.All(c => c is >= '0' and <= '9' or >= 'a' and <= 'f');
}
