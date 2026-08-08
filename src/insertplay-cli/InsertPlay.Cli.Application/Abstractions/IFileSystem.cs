namespace InsertPlay.Cli.Application.Abstractions;

public interface IFileSystem
{
    bool FileExists(string path);
    bool DirectoryExists(string path);
    void CreateDirectory(string path);
    void CopyFile(string sourcePath, string destinationPath, bool overwrite);
    void DeleteFile(string path);
    void MoveFile(string sourcePath, string destinationPath, bool overwrite);
    string ReadAllText(string path);
    void WriteAllText(string path, string contents);
    string[] ReadAllLines(string path);
    void WriteAllLines(string path, IEnumerable<string> lines);
    string[] GetDirectories(string path);
    string GetFullPath(string path);
}
