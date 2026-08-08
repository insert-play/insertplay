using System.Text.Json;
using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Domain.Configuration;

namespace InsertPlay.Cli.Infrastructure.Config;

public sealed class JsonConfigStore(IFileSystem fileSystem, IEnvironmentFileStore environmentFileStore) : IConfigStore
{
    private const string ConfigDirName = "insertplay";
    private const string ConfigFileName = "config.json";
    private const string RuntimeEnvFileName = "runtime.env";
    private const string RuntimeShortcutsDirName = "shortcuts";

    public string GetConfigDirectoryPath()
    {
        var xdgConfigHome = Environment.GetEnvironmentVariable("XDG_CONFIG_HOME");
        var baseDir = string.IsNullOrWhiteSpace(xdgConfigHome)
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".config")
            : xdgConfigHome;

        return Path.Combine(baseDir, ConfigDirName);
    }

    public string GetConfigPath() => Path.Combine(GetConfigDirectoryPath(), ConfigFileName);

    public string GetRuntimeEnvPath() => Path.Combine(GetConfigDirectoryPath(), RuntimeEnvFileName);

    public string GetRuntimeShortcutsDirectoryPath() => Path.Combine(GetConfigDirectoryPath(), RuntimeShortcutsDirName);

    public string GetRuntimeShortcutEnvPath(string shortcutGuid)
    {
        if (!Guid.TryParse(shortcutGuid, out var parsedGuid))
            throw new ArgumentException("shortcutGuid must be a valid UUID/GUID.", nameof(shortcutGuid));

        var normalizedGuid = parsedGuid.ToString("D");
        return Path.Combine(GetRuntimeShortcutsDirectoryPath(), $"{normalizedGuid}.env");
    }

    public AppConfig? TryLoad()
    {
        var path = GetConfigPath();
        if (!fileSystem.FileExists(path))
            return null;

        var json = fileSystem.ReadAllText(path);
        return JsonSerializer.Deserialize<AppConfig>(json, JsonOptions());
    }

    public void Save(AppConfig config)
    {
        var path = GetConfigPath();
        fileSystem.CreateDirectory(Path.GetDirectoryName(path)!);

        var now = DateTimeOffset.UtcNow;
        if (config.CreatedAt == default)
            config.CreatedAt = now;
        config.UpdatedAt = now;

        var json = JsonSerializer.Serialize(config, JsonOptions());
        fileSystem.WriteAllText(path, json);
    }

    public void SaveRuntimeEnvironment(IReadOnlyDictionary<string, string> variables) =>
        environmentFileStore.Write(GetRuntimeEnvPath(), variables);

    public string SaveRuntimeShortcutEnvironment(string shortcutGuid, IReadOnlyDictionary<string, string> variables)
    {
        var path = GetRuntimeShortcutEnvPath(shortcutGuid);
        environmentFileStore.Write(path, variables);
        return path;
    }

    public bool IsValid(AppConfig? config, out string error)
    {
        if (config is null)
        {
            error = "Configuration not found. Run: insertplay setup";
            return false;
        }

        if (config.SchemaVersion != 1)
        {
            error = $"Unsupported configuration schema version: {config.SchemaVersion}";
            return false;
        }

        if (string.IsNullOrWhiteSpace(config.SteamUserId))
        {
            error = "steamUserId is empty in the configuration.";
            return false;
        }

        if (string.IsNullOrWhiteSpace(config.SteamPath) || !fileSystem.DirectoryExists(config.SteamPath))
        {
            error = "steamPath is invalid in the configuration.";
            return false;
        }

        error = string.Empty;
        return true;
    }

    private static JsonSerializerOptions JsonOptions() => new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true
    };
}
