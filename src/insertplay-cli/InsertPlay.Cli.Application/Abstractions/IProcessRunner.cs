namespace InsertPlay.Cli.Application.Abstractions;

public interface IProcessRunner
{
    bool TryRun(string fileName, string arguments, bool waitForExit, int timeoutMs);

    bool TryRunAndGetExitCode(string fileName, string arguments, int timeoutMs, out int exitCode);

    bool IsProcessRunning(string processName);
}
