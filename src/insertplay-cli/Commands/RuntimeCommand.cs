using InsertPlay.Cli.Application.UseCases.Runtime;
using InsertPlay.Cli.Common;

namespace InsertPlay.Cli.Commands;

internal sealed class RuntimeCommand(
    InstallSdShortcutUseCase installUseCase,
    AutorunLaunchUseCase autorunUseCase,
    EnsureShortcutIdentityUseCase ensureIdentityUseCase) : ICommand
{
    public int Execute(string[] argv)
    {
        if (argv.Length < 2)
        {
            Console.Error.WriteLine("Usage: insertplay runtime <install-shortcut|autorun-launch|ensure-identity> [options]");
            return 1;
        }

        return argv[1] switch
        {
            "install-shortcut" => InstallShortcut(argv),
            "autorun-launch" => AutorunLaunch(argv),
            "ensure-identity" => EnsureIdentity(argv),
            _ => 1
        };
    }

    private int InstallShortcut(string[] argv)
    {
        var sdRootRaw = ArgvParser.GetOption(argv, "--sd-root");
        var restartSteam = ArgvParser.HasFlag(argv, "--restart-steam");

        var result = installUseCase.Execute(sdRootRaw, restartSteam);

        if (result.ErrorMessage is not null)
            Console.Error.WriteLine(result.ErrorMessage);

        foreach (var line in result.StandardOutputLines)
            Console.WriteLine(line);

        if (result.RestartMessage is not null)
        {
            if (result.RestartFailed)
                Console.Error.WriteLine(result.RestartMessage);
            else
                Console.WriteLine(result.RestartMessage);
        }

        return result.ExitCode;
    }

    private int AutorunLaunch(string[] argv)
    {
        var sdRootRaw = ArgvParser.GetOption(argv, "--sd-root");
        var explicitGuid = ArgvParser.GetOption(argv, "--guid");

        var result = autorunUseCase.Execute(sdRootRaw, explicitGuid);

        foreach (var line in result.InfoLines)
            Console.WriteLine(line);

        if (result.ErrorMessage is not null)
            Console.Error.WriteLine(result.ErrorMessage);

        return result.ExitCode;
    }

    private int EnsureIdentity(string[] argv)
    {
        var sdRootRaw = ArgvParser.GetOption(argv, "--sd-root");
        var result = ensureIdentityUseCase.Execute(sdRootRaw);

        if (!result.Success)
        {
            Console.Error.WriteLine(result.ErrorMessage);
            return result.ExitCode;
        }

        Console.WriteLine($"Shortcut identity ensured with GUID {result.ShortcutGuid} at {result.IdentityPath}");
        return 0;
    }
}
