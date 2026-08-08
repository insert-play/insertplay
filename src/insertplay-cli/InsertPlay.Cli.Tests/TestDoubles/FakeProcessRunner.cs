using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Tests.TestDoubles;

internal sealed class FakeProcessRunner : IProcessRunner
{
    public bool TryRunResult { get; set; } = true;
    public bool TryRunAndGetExitCodeResult { get; set; } = true;
    public int ExitCode { get; set; }
    public bool ProcessRunning { get; set; }
    public List<(string FileName, string Arguments)> RunCalls { get; } = [];

    public bool TryRun(string fileName, string arguments, bool waitForExit, int timeoutMs)
    {
        RunCalls.Add((fileName, arguments));
        return TryRunResult;
    }

    public bool TryRunAndGetExitCode(string fileName, string arguments, int timeoutMs, out int exitCode)
    {
        exitCode = ExitCode;
        return TryRunAndGetExitCodeResult;
    }

    public bool IsProcessRunning(string processName) => ProcessRunning;
}
