using System.Diagnostics;
using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Infrastructure.Processes;

public sealed class SystemProcessRunner : IProcessRunner
{
    public bool TryRun(string fileName, string arguments, bool waitForExit, int timeoutMs)
    {
        try
        {
            var process = Process.Start(new ProcessStartInfo
            {
                FileName = fileName,
                Arguments = arguments,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            });

            if (process is null)
                return false;

            if (waitForExit && !process.WaitForExit(timeoutMs))
                return false;

            return true;
        }
        catch
        {
            return false;
        }
    }

    public bool TryRunAndGetExitCode(string fileName, string arguments, int timeoutMs, out int exitCode)
    {
        exitCode = -1;

        try
        {
            var process = Process.Start(new ProcessStartInfo
            {
                FileName = fileName,
                Arguments = arguments,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            });

            if (process is null)
                return false;

            if (!process.WaitForExit(timeoutMs))
                return false;

            exitCode = process.ExitCode;
            return true;
        }
        catch
        {
            return false;
        }
    }

    public bool IsProcessRunning(string processName)
    {
        try
        {
            return Process.GetProcessesByName(processName).Length > 0;
        }
        catch
        {
            return false;
        }
    }
}
