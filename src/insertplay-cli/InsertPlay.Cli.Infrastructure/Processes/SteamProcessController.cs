using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Infrastructure.Processes;

public sealed class SteamProcessController(IProcessRunner processRunner, IFileSystem fileSystem) : ISteamProcessController
{
    public bool Restart(string steamPath, out string message)
    {
        if (OperatingSystem.IsLinux())
        {
            processRunner.TryRun("steam", "-shutdown", waitForExit: true, timeoutMs: 10000);
            WaitForSteamToStop(timeoutMs: 15000);

            var steamSh = Path.Combine(steamPath, "steam.sh");
            if (fileSystem.FileExists(steamSh) && TryLaunchSteamLinux($"\"{steamSh}\""))
            {
                message = "Steam restart requested (steam.sh).";
                return true;
            }

            if (TryLaunchSteamLinux("steam"))
            {
                message = "Steam restart requested (steam command).";
                return true;
            }

            if (TryLaunchSteamLinux("flatpak run com.valvesoftware.Steam"))
            {
                message = "Steam restart requested (flatpak).";
                return true;
            }

            if (TryLaunchSteamLinux("xdg-open steam://open/main"))
            {
                message = "Steam restart requested (steam URI).";
                return true;
            }

            message = "Could not start Steam process automatically.";
            return false;
        }

        if (OperatingSystem.IsWindows())
        {
            processRunner.TryRun("steam", "-shutdown", waitForExit: true, timeoutMs: 10000);
            Thread.Sleep(1500);

            var steamExe = Path.Combine(steamPath, "steam.exe");
            if (fileSystem.FileExists(steamExe) && processRunner.TryRun(steamExe, string.Empty, waitForExit: false, timeoutMs: 0))
            {
                message = "Steam restart requested (steam.exe).";
                return true;
            }

            if (processRunner.TryRun("steam", string.Empty, waitForExit: false, timeoutMs: 0))
            {
                message = "Steam restart requested (steam command).";
                return true;
            }

            message = "Could not start Steam process automatically.";
            return false;
        }

        message = "Steam restart is not supported on this operating system.";
        return false;
    }

    public bool LaunchGame(string gameId, out string message)
    {
        if (!ulong.TryParse(gameId, out _))
        {
            message = "Game ID is invalid.";
            return false;
        }

        var uri = $"steam://rungameid/{gameId}";
        if (processRunner.TryRun("steam", uri, waitForExit: false, timeoutMs: 0))
        {
            message = "launch requested via steam command";
            return true;
        }

        if (processRunner.TryRun("xdg-open", uri, waitForExit: false, timeoutMs: 0))
        {
            message = "launch requested via xdg-open";
            return true;
        }

        message = "steam command and xdg-open both failed";
        return false;
    }

    private bool TryLaunchSteamLinux(string launchCommand)
    {
        var escapedCommand = launchCommand.Replace("\"", "\\\"");
        var shellArgs = $"-lc \"nohup {escapedCommand} >/dev/null 2>&1 &\"";

        if (!processRunner.TryRun("/bin/bash", shellArgs, waitForExit: true, timeoutMs: 5000))
            return false;

        return WaitForSteamToStart(timeoutMs: 15000);
    }

    private bool WaitForSteamToStart(int timeoutMs)
    {
        var startedAt = Environment.TickCount64;
        while (Environment.TickCount64 - startedAt < timeoutMs)
        {
            if (processRunner.IsProcessRunning("steam"))
                return true;

            Thread.Sleep(500);
        }

        return false;
    }

    private bool WaitForSteamToStop(int timeoutMs)
    {
        var startedAt = Environment.TickCount64;
        while (Environment.TickCount64 - startedAt < timeoutMs)
        {
            if (!processRunner.IsProcessRunning("steam"))
                return true;

            Thread.Sleep(500);
        }

        return false;
    }
}
