using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Infrastructure.Processes;

public sealed class ProcessMonitor(IProcessRunner processRunner) : IProcessMonitor
{
    public bool IsProcessRunningPattern(string processPattern)
    {
        if (string.IsNullOrWhiteSpace(processPattern))
            return false;

        var escapedPattern = processPattern.Replace("'", "'\"'\"'");
        var shellCommand = $"pgrep -f -- '{escapedPattern}' >/dev/null 2>&1";
        var shellArgs = $"-lc \"{shellCommand}\"";

        if (!processRunner.TryRunAndGetExitCode("/bin/bash", shellArgs, timeoutMs: 3000, out var exitCode))
            return false;

        return exitCode == 0;
    }

    public bool WaitForProcessPattern(string processPattern, int timeoutSeconds)
    {
        for (var i = 0; i < timeoutSeconds; i++)
        {
            if (IsProcessRunningPattern(processPattern))
                return true;

            Thread.Sleep(1000);
        }

        return false;
    }
}
