using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Tests.TestDoubles;

internal sealed class FakeShortcutIdentityStore : IShortcutIdentityStore
{
    public bool GetOrCreateResult { get; set; } = true;
    public string GetOrCreateGuid { get; set; } = "00000000-0000-0000-0000-000000000000";
    public string GetOrCreateError { get; set; } = string.Empty;

    public bool ReadResult { get; set; }
    public string ReadGuid { get; set; } = string.Empty;
    public string ReadError { get; set; } = string.Empty;

    public bool TryGetOrCreate(string sdRoot, string appName, string? manifestGuid, out string shortcutGuid, out string error)
    {
        shortcutGuid = GetOrCreateGuid;
        error = GetOrCreateError;
        return GetOrCreateResult;
    }

    public bool TryRead(string sdRoot, out string shortcutGuid, out string error)
    {
        shortcutGuid = ReadGuid;
        error = ReadError;
        return ReadResult;
    }
}
