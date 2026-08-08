using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Infrastructure.Assets;

public sealed class BundledAssetLocator : IBundledAssetLocator
{
    public string BaseDirectory => AppContext.BaseDirectory;

    public string? ResolveOutputFilePath(params string[] candidates)
    {
        foreach (var candidate in candidates)
        {
            var fullPath = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, candidate));
            if (File.Exists(fullPath))
                return fullPath;
        }

        return null;
    }
}
