#:property PublishAot=false
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Text.RegularExpressions;
using System.Xml.Linq;

if (args.Length != 3)
    throw new ArgumentException("Expected the verified HIDMaestro core assembly, the resource map and an output directory.");

var wanted = XDocument.Load(args[1]).Descendants("EmbeddedResource").ToDictionary(
    item => (string)item.Element("LogicalName")!,
    item => Path.GetFileName((string)item.Attribute("Include")!),
    StringComparer.Ordinal);
if (wanted.Values.Distinct(StringComparer.OrdinalIgnoreCase).Count() != wanted.Count ||
    wanted.Values.Any(name => !Regex.IsMatch(name, @"\Aresource-[0-9]{4}\.bin\z")))
    throw new InvalidDataException("The controller resource map contains an invalid destination.");

// Read PE metadata directly. No upstream assembly code is loaded or executed.
using var assembly = new PEReader(File.OpenRead(args[0]));
var directory = assembly.PEHeaders.CorHeader!.ResourcesDirectory;
var data = assembly.GetSectionData(directory.RelativeVirtualAddress);
var metadata = assembly.GetMetadataReader();
var restored = new Dictionary<string, byte[]>();
foreach (var handle in metadata.ManifestResources)
{
    var resource = metadata.GetManifestResource(handle);
    if (!wanted.TryGetValue(metadata.GetString(resource.Name), out var filename))
        continue;
    if (!resource.Implementation.IsNil || resource.Offset < 0 || resource.Offset > directory.Size - sizeof(int))
        throw new InvalidDataException($"Invalid embedded resource location for {filename}.");
    var offset = checked((int)resource.Offset);
    var reader = data.GetReader(offset, directory.Size - offset);
    var length = reader.ReadInt32();
    if (length < 0 || length > reader.RemainingBytes)
        throw new InvalidDataException($"Invalid embedded resource length for {filename}.");
    restored.Add(filename, reader.ReadBytes(length));
}
var missing = wanted.Where(entry => !restored.ContainsKey(entry.Value)).Select(entry => entry.Key).ToArray();
if (missing.Length != 0)
    throw new InvalidDataException("Missing pinned HIDMaestro resources: " + string.Join(", ", missing));

Directory.CreateDirectory(args[2]);
foreach (var (filename, content) in restored)
    File.WriteAllBytes(Path.Combine(args[2], filename), content);
