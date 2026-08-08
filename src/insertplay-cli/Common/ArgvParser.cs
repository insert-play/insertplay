namespace InsertPlay.Cli.Common;

internal static class ArgvParser
{
    public static string? GetOption(string[] argv, string key)
    {
        for (var i = 0; i < argv.Length - 1; i++)
        {
            if (argv[i].Equals(key, StringComparison.OrdinalIgnoreCase))
                return argv[i + 1];
        }

        return null;
    }

    public static bool HasFlag(string[] argv, string key) =>
        argv.Any(arg => arg.Equals(key, StringComparison.OrdinalIgnoreCase));
}
