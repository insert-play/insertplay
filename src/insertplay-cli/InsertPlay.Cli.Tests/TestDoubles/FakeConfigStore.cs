using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Domain.Configuration;

namespace InsertPlay.Cli.Tests.TestDoubles;

internal sealed class FakeConfigStore : IConfigStore
{
    public AppConfig? Config { get; set; }
    public bool ValidResult { get; set; } = true;
    public string ValidError { get; set; } = string.Empty;
    public Dictionary<string, string>? LastSavedRuntimeEnvironment { get; private set; }
    public Dictionary<string, Dictionary<string, string>> SavedShortcutEnvironments { get; } = new();

    public string GetConfigPath() => "/config/config.json";

    public AppConfig? TryLoad() => Config;

    public void Save(AppConfig config) => Config = config;

    public bool IsValid(AppConfig? config, out string error)
    {
        error = ValidError;
        return ValidResult;
    }

    public string GetRuntimeEnvPath() => "/config/runtime.env";

    public void SaveRuntimeEnvironment(IReadOnlyDictionary<string, string> variables) =>
        LastSavedRuntimeEnvironment = new Dictionary<string, string>(variables);

    public string GetRuntimeShortcutsDirectoryPath() => "/config/shortcuts";

    public string GetRuntimeShortcutEnvPath(string shortcutGuid)
    {
        if (!Guid.TryParse(shortcutGuid, out var parsed))
            throw new ArgumentException("shortcutGuid must be a valid UUID/GUID.", nameof(shortcutGuid));

        return $"/config/shortcuts/{parsed:D}.env";
    }

    public string SaveRuntimeShortcutEnvironment(string shortcutGuid, IReadOnlyDictionary<string, string> variables)
    {
        var path = GetRuntimeShortcutEnvPath(shortcutGuid);
        SavedShortcutEnvironments[shortcutGuid] = new Dictionary<string, string>(variables);
        return path;
    }
}
