using InsertPlay.Cli.Domain.Configuration;

namespace InsertPlay.Cli.Application.Abstractions;

public interface IConfigStore
{
    string GetConfigPath();
    AppConfig? TryLoad();
    void Save(AppConfig config);
    bool IsValid(AppConfig? config, out string error);

    string GetRuntimeEnvPath();
    void SaveRuntimeEnvironment(IReadOnlyDictionary<string, string> variables);

    string GetRuntimeShortcutsDirectoryPath();
    string GetRuntimeShortcutEnvPath(string shortcutGuid);
    string SaveRuntimeShortcutEnvironment(string shortcutGuid, IReadOnlyDictionary<string, string> variables);
}
