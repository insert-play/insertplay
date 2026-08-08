namespace InsertPlay.Cli;

internal static class HelpText
{
    public static void Print()
    {
        Console.WriteLine("InsertPlay CLI (MVP)");
        Console.WriteLine("Commands:");
        Console.WriteLine("  setup");
        Console.WriteLine("  users");
        Console.WriteLine("  configure --user <steamUserId>");
        Console.WriteLine("  shortcut add --name <name> --exe <path> [--startdir <dir>] [--args <args>] [--icon <path>] [--hidden <true|false>]");
        Console.WriteLine("  shortcut update --name <name> --exe <path> [--startdir <dir>] [--args <args>] [--icon <path>] [--hidden <true|false>]");
        Console.WriteLine("  shortcut remove --name <name>");
        Console.WriteLine("  shortcut list");
        Console.WriteLine("  steam restart");
        Console.WriteLine("  runtime install-shortcut --sd-root <path> [--restart-steam]");
        Console.WriteLine("  runtime autorun-launch --sd-root <path> [--guid <guid>]");
        Console.WriteLine("  runtime ensure-identity --sd-root <path>");
    }
}
