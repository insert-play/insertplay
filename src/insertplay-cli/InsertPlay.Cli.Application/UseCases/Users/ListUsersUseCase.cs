using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Application.UseCases.Users;

public sealed record ListUsersResult(
    bool SteamFound,
    string? Error,
    int ExitCode,
    string? ConfiguredUserId,
    IReadOnlyList<SteamUserInfo> Users);

public sealed class ListUsersUseCase(
    IConfigStore configStore,
    ISteamInstallationLocator steamLocator,
    IFileSystem fileSystem)
{
    public ListUsersResult Execute()
    {
        var cfg = configStore.TryLoad();
        var steamRoot = cfg?.SteamPath;

        if (string.IsNullOrWhiteSpace(steamRoot) || !fileSystem.DirectoryExists(steamRoot))
            steamRoot = steamLocator.FindRoot();

        if (string.IsNullOrWhiteSpace(steamRoot))
            return new ListUsersResult(false, "Steam not found.", 2, cfg?.SteamUserId, []);

        var users = steamLocator.ListUsers(steamRoot);
        return new ListUsersResult(true, null, 0, cfg?.SteamUserId, users);
    }
}
