using System.Text.RegularExpressions;
using InsertPlay.Cli.Models;
using Microsoft.Win32;

namespace InsertPlay.Cli.Steam;

public static partial class SteamPaths
{
    private static readonly Regex UserBlockRegex =
        new("\"(\\d{10,})\"\\s*\\{([^{}]*)\\}", RegexOptions.Singleline | RegexOptions.Compiled);

    private static readonly Regex VdfValueRegex =
        new("\"(\\w+)\"\\s+\"([^\"]*)\"", RegexOptions.Compiled);

    public static string? FindSteamRoot()
    {
        if (OperatingSystem.IsWindows())
        {
            using var key = Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam");
            return key?.GetValue("SteamPath") as string;
        }

        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var candidates = new[]
        {
            Path.Combine(home, ".steam", "steam"),
            Path.Combine(home, ".local", "share", "Steam")
        };

        return candidates.FirstOrDefault(Directory.Exists);
    }

    public static string? FindShortcutsVdf(string steamRoot, string userId)
    {
        var path = Path.Combine(steamRoot, "userdata", userId, "config", "shortcuts.vdf");
        return File.Exists(path) ? path : null;
    }

    public static string GetOrCreateShortcutsVdfPath(string steamRoot, string userId)
    {
        var existing = FindShortcutsVdf(steamRoot, userId);
        if (existing is not null)
            return existing;

        var dir = Path.Combine(steamRoot, "userdata", userId, "config");
        Directory.CreateDirectory(dir);
        return Path.Combine(dir, "shortcuts.vdf");
    }

    public static IReadOnlyList<SteamUserInfo> ListUsers(string steamRoot)
    {
        const long steam64Offset = 76561197960265728L;

        var userdataDir = Path.Combine(steamRoot, "userdata");
        if (!Directory.Exists(userdataDir))
            return [];

        var loginUsersPath = Path.Combine(steamRoot, "config", "loginusers.vdf");
        var loginUsers = File.Exists(loginUsersPath)
            ? ParseLoginUsersVdf(File.ReadAllText(loginUsersPath))
            : [];

        return Directory.GetDirectories(userdataDir)
            .Select(Path.GetFileName)
            .Where(name => long.TryParse(name, out _))
            .Select(accountId =>
            {
                var steam64 = (long.Parse(accountId!) + steam64Offset).ToString();
                loginUsers.TryGetValue(steam64, out var entry);
                return new SteamUserInfo(
                    UserId: accountId!,
                    AccountName: entry?.AccountName ?? accountId!,
                    PersonaName: entry?.PersonaName ?? accountId!,
                    MostRecent: entry?.MostRecent ?? false);
            })
            .OrderByDescending(u => u.MostRecent)
            .ThenBy(u => u.AccountName)
            .ToList();
    }

    private sealed record LoginUserEntry(string AccountName, string PersonaName, bool MostRecent);

    private static Dictionary<string, LoginUserEntry> ParseLoginUsersVdf(string content)
    {
        Dictionary<string, LoginUserEntry> result = [];

        foreach (Match block in UserBlockRegex.Matches(content))
        {
            var steam64 = block.Groups[1].Value;
            var values = VdfValueRegex.Matches(block.Groups[2].Value)
                .ToDictionary(
                    m => m.Groups[1].Value,
                    m => m.Groups[2].Value,
                    StringComparer.OrdinalIgnoreCase);

            result[steam64] = new LoginUserEntry(
                AccountName: values.GetValueOrDefault("AccountName", steam64),
                PersonaName: values.GetValueOrDefault("PersonaName", steam64),
                MostRecent: values.GetValueOrDefault("MostRecent", "0") == "1");
        }

        return result;
    }
}
