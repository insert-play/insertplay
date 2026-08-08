using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Tests.TestDoubles;

internal sealed class FakeBundledAssetLocator : IBundledAssetLocator
{
    public string BaseDirectory { get; set; } = "/app";
    public Dictionary<string, string> Files { get; } = new();

    public string? ResolveOutputFilePath(params string[] candidates)
    {
        foreach (var candidate in candidates)
        {
            if (Files.TryGetValue(candidate, out var resolved))
                return resolved;
        }

        return null;
    }
}
