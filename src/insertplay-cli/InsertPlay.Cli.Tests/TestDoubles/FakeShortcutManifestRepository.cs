using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Tests.TestDoubles;

internal sealed class FakeShortcutManifestRepository : IShortcutManifestRepository
{
    public bool Result { get; set; } = true;
    public SteamShortcutManifest? Manifest { get; set; }
    public string Error { get; set; } = string.Empty;

    public bool TryLoad(string path, out SteamShortcutManifest? manifest, out string error)
    {
        manifest = Manifest;
        error = Error;
        return Result;
    }
}
