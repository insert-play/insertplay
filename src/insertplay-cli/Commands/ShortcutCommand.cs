using InsertPlay.Cli.Application.UseCases.Shortcuts;
using InsertPlay.Cli.Common;

namespace InsertPlay.Cli.Commands;

internal sealed class ShortcutCommand(
    AddOrUpdateShortcutUseCase addOrUpdateUseCase,
    RemoveShortcutUseCase removeUseCase,
    ListShortcutsUseCase listUseCase) : ICommand
{
    public int Execute(string[] argv)
    {
        if (argv.Length < 2)
        {
            Console.Error.WriteLine("Usage: insertplay shortcut <add|update|remove|list> [options]");
            return 1;
        }

        return argv[1] switch
        {
            "add" or "update" => Upsert(argv),
            "remove" => Remove(argv),
            "list" => List(),
            _ => 1
        };
    }

    private int Upsert(string[] argv)
    {
        var name = ArgvParser.GetOption(argv, "--name");
        var exe = ArgvParser.GetOption(argv, "--exe");
        var startDir = ArgvParser.GetOption(argv, "--startdir") ?? string.Empty;
        var launchOptions = ArgvParser.GetOption(argv, "--args") ?? string.Empty;
        var icon = ArgvParser.GetOption(argv, "--icon") ?? string.Empty;
        var hiddenRaw = ArgvParser.GetOption(argv, "--hidden");
        var isHidden = false;

        if (!string.IsNullOrWhiteSpace(hiddenRaw) && !bool.TryParse(hiddenRaw, out isHidden))
        {
            Console.Error.WriteLine("Invalid value for --hidden. Use true or false.");
            return 1;
        }

        if (string.IsNullOrWhiteSpace(name) || string.IsNullOrWhiteSpace(exe))
        {
            Console.Error.WriteLine("Usage: insertplay shortcut add --name <name> --exe <path> [--startdir <dir>] [--args <args>] [--icon <icon>] [--hidden <true|false>]");
            return 1;
        }

        var result = addOrUpdateUseCase.Execute(name, exe, startDir, launchOptions, icon, isHidden);
        Console.WriteLine(result.Message);
        return result.ExitCode;
    }

    private int Remove(string[] argv)
    {
        var name = ArgvParser.GetOption(argv, "--name");
        if (string.IsNullOrWhiteSpace(name))
        {
            Console.Error.WriteLine("Usage: insertplay shortcut remove --name <name>");
            return 1;
        }

        var result = removeUseCase.Execute(name);
        Console.WriteLine(result.Message);
        return result.ExitCode;
    }

    private int List()
    {
        var result = listUseCase.Execute();
        if (!result.Success)
        {
            Console.Error.WriteLine(result.Error);
            return result.ExitCode;
        }

        if (result.Items.Count == 0)
        {
            Console.WriteLine("No shortcuts found.");
            return 0;
        }

        foreach (var item in result.Items)
            Console.WriteLine($"{item.AppName} | appId={item.AppId} | exe={item.Exe}");

        return 0;
    }
}
