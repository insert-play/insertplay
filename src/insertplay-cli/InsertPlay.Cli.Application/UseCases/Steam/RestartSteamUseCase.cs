using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Application.UseCases.Steam;

public sealed record RestartSteamResult(bool Success, string Message, int ExitCode);

public sealed class RestartSteamUseCase(
    IConfigStore configStore,
    ISteamInstallationLocator steamLocator,
    IFileSystem fileSystem,
    ISteamProcessController steamProcessController)
{
    public RestartSteamResult Execute()
    {
        var cfg = configStore.TryLoad();
        var steamPath = cfg?.SteamPath;

        if (string.IsNullOrWhiteSpace(steamPath) || !fileSystem.DirectoryExists(steamPath))
            steamPath = steamLocator.FindRoot();

        if (string.IsNullOrWhiteSpace(steamPath) || !fileSystem.DirectoryExists(steamPath))
            return new RestartSteamResult(false, "Steam path not found. Run 'insertplay setup' first or ensure Steam is installed.", 2);

        if (!steamProcessController.Restart(steamPath, out var message))
            return new RestartSteamResult(false, $"Steam restart failed: {message}", 20);

        return new RestartSteamResult(true, message, 0);
    }
}
