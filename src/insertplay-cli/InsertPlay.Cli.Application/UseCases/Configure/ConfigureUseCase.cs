using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Application.Common;
using InsertPlay.Cli.Application.Services;
using InsertPlay.Cli.Domain.Configuration;
using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Application.UseCases.Configure;

public sealed record ConfigureResult(bool Success, string? Error, int ExitCode, string? RuntimeEnvPath);

public sealed class ConfigureUseCase(
    IConfigStore configStore,
    ISteamInstallationLocator steamLocator,
    IFileSystem fileSystem,
    RuntimeEnvironmentWriter runtimeEnvironmentWriter)
{
    public ConfigureResult Execute(string userId)
    {
        var cfg = configStore.TryLoad();
        var steamRoot = cfg?.SteamPath;

        if (string.IsNullOrWhiteSpace(steamRoot) || !fileSystem.DirectoryExists(steamRoot))
            steamRoot = steamLocator.FindRoot();

        if (string.IsNullOrWhiteSpace(steamRoot))
            return new ConfigureResult(false, "Steam not found.", 2, null);

        var users = steamLocator.ListUsers(steamRoot);
        if (!users.Any(u => u.UserId == userId))
            return new ConfigureResult(false, "The specified user was not found in userdata.", 3, null);

        cfg ??= new AppConfig();
        cfg.SteamPath = steamRoot;
        cfg.SteamUserId = userId;
        cfg.SchemaVersion = 1;
        configStore.Save(cfg);

        var appId = AppIdCalculator.Calculate(StartSdShortcutDefaults.ExePath, StartSdShortcutDefaults.AppName);
        var runtimeEnvPath = runtimeEnvironmentWriter.Save(cfg, appId);

        return new ConfigureResult(true, null, 0, runtimeEnvPath);
    }
}
