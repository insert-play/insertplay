using InsertPlay.Cli.Commands;

namespace InsertPlay.Cli;

internal sealed class CommandRouter(
    SetupCommand setupCommand,
    UsersCommand usersCommand,
    ConfigureCommand configureCommand,
    ShortcutCommand shortcutCommand,
    SteamCommand steamCommand,
    RuntimeCommand runtimeCommand)
{
    public int Run(string[] args)
    {
        if (args.Length == 0)
        {
            HelpText.Print();
            return 0;
        }

        return args[0] switch
        {
            "setup" => setupCommand.Execute(args),
            "users" => usersCommand.Execute(args),
            "configure" => configureCommand.Execute(args),
            "shortcut" => shortcutCommand.Execute(args),
            "steam" => steamCommand.Execute(args),
            "runtime" => runtimeCommand.Execute(args),
            "help" or "--help" or "-h" => PrintAndExit(0),
            _ => PrintAndExit(1)
        };
    }

    private static int PrintAndExit(int code)
    {
        HelpText.Print();
        return code;
    }
}
