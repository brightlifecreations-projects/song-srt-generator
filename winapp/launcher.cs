// SRT Generator.exe — the Windows app's entry point (DECISIONS.md V4.7). Compiled by
// make_release_windows.ps1 with the C# compiler that ships with Windows (.NET Framework 4).
//
// Everything writable lives in %LOCALAPPDATA%\SRT Generator (python, venv, models, jobs, settings).
// On the very first launch (or if the venv is gone) this creates the private Python and venv with
// the bundled uv and installs the packages the window needs, behind a small splash. Then it starts
// winapp\launcher.py with the venv's pythonw.exe and exits; launcher.py shows the real setup window
// (packages, models, test run) and the app. A second launch goes straight to launcher.py, which
// focuses the running window.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Windows.Forms;

static class SrtLauncher
{
    // The packages launcher.py needs before its own setup window can open (pinned in requirements-windows.txt).
    static readonly string[] WindowPackages = {
        "pywebview", "pythonnet", "clr-loader", "proxy-tools", "bottle", "cffi", "pycparser", "typing-extensions" };

    static string appDir, dataDir, logPath;

    [STAThread]
    static int Main(string[] args)
    {
        appDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        // SRT_DATA_DIR (tests only) points the app at another data folder.
        dataDir = Environment.GetEnvironmentVariable("SRT_DATA_DIR");
        if (string.IsNullOrEmpty(dataDir))
            dataDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SRT Generator");
        Directory.CreateDirectory(dataDir);
        logPath = Path.Combine(dataDir, "install.log");
        string pythonw = Path.Combine(dataDir, @"venv\Scripts\pythonw.exe");
        string marker = Path.Combine(dataDir, @"venv\window-packages.txt");
        string wanted = string.Join("\n", PinnedWindowPackages());

        bool ready = File.Exists(pythonw) && File.Exists(marker) && File.ReadAllText(marker) == wanted;
        if (!ready)
        {
            string error = null;
            RunWithSplash(() => error = Bootstrap(wanted, marker));
            if (error != null)
            {
                MessageBox.Show(error + "\n\nThe details are in:\n" + logPath, "SRT Generator",
                                MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
        }

        var start = new ProcessStartInfo(pythonw, Quote(Path.Combine(appDir, @"winapp\launcher.py")))
        {
            UseShellExecute = false,
            WorkingDirectory = dataDir,
        };
        SetEnv(start.EnvironmentVariables);
        Process.Start(start);
        return 0;
    }

    static List<string> PinnedWindowPackages()
    {
        var pins = new List<string>();
        foreach (var line in File.ReadAllLines(Path.Combine(appDir, "requirements-windows.txt")))
        {
            string name = line.Split(new[] { "==" }, StringSplitOptions.None)[0].Trim().ToLowerInvariant();
            if (WindowPackages.Contains(name)) pins.Add(line.Trim());
        }
        return pins;
    }

    static void SetEnv(System.Collections.Specialized.StringDictionary env)
    {
        env["SRT_APP_DIR"] = appDir;
        env["SRT_DATA_DIR"] = dataDir;
        env["PATH"] = Path.Combine(appDir, "bin") + ";" + Environment.GetEnvironmentVariable("PATH");
        env["UV_PYTHON_INSTALL_DIR"] = Path.Combine(dataDir, "python");
        env["UV_PYTHON_PREFERENCE"] = "only-managed";
        env["UV_CACHE_DIR"] = Path.Combine(dataDir, "uv-cache");
        env["HF_HOME"] = Path.Combine(dataDir, @"models\hf");
        env["PYTHONUTF8"] = "1";
    }

    // The private Python 3.10 and venv, and the window's packages. Returns an error message or null.
    static string Bootstrap(string wanted, string marker)
    {
        string uv = Path.Combine(appDir, @"bin\uv.exe");
        string venv = Path.Combine(dataDir, "venv");
        if (!File.Exists(Path.Combine(venv, @"Scripts\python.exe")))
        {
            if (Run(uv, "venv --python 3.10 " + Quote(venv)) != 0)
                return "Couldn't set up the app's Python. Check your internet connection and open SRT Generator again.";
        }
        var pkgArgs = new StringBuilder("pip install --python " + Quote(Path.Combine(venv, @"Scripts\python.exe")));
        string wheels = Path.Combine(appDir, "wheels");  // prebuilt wheels for source-only packages
        if (Directory.Exists(wheels)) pkgArgs.Append(" --find-links " + Quote(wheels));
        foreach (var pin in wanted.Split('\n')) pkgArgs.Append(" " + Quote(pin));
        if (Run(uv, pkgArgs.ToString()) != 0)
            return "Couldn't install the app's window. Check your internet connection and open SRT Generator again.";
        File.WriteAllText(marker, wanted);
        return null;
    }

    static int Run(string exe, string arguments)
    {
        var info = new ProcessStartInfo(exe, arguments)
        {
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true,
            RedirectStandardError = true, WorkingDirectory = dataDir,
        };
        SetEnv(info.EnvironmentVariables);
        using (var log = new StreamWriter(logPath, true, new UTF8Encoding(false)))
        {
            log.WriteLine("=== " + DateTime.Now + "  " + exe + " " + arguments);
            var p = Process.Start(info);
            string output = p.StandardOutput.ReadToEndAsync().Result + p.StandardError.ReadToEnd();
            p.WaitForExit();
            log.WriteLine(output);
            log.WriteLine("exit " + p.ExitCode);
            return p.ExitCode;
        }
    }

    static string Quote(string s) { return "\"" + s + "\""; }

    // A small window while the private Python is prepared (only on the first launch).
    static void RunWithSplash(Action work)
    {
        Application.EnableVisualStyles();
        var form = new Form
        {
            Text = "SRT Generator", Width = 460, Height = 170, FormBorderStyle = FormBorderStyle.FixedDialog,
            StartPosition = FormStartPosition.CenterScreen, MaximizeBox = false, MinimizeBox = false,
            BackColor = Color.FromArgb(0xF5, 0xF6, 0xF9),
        };
        try { form.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
        var label = new Label
        {
            Text = "Getting SRT Generator ready…\nThis first launch sets up the app's own Python (about a minute).",
            Left = 24, Top = 22, Width = 400, Height = 48, Font = new Font("Segoe UI", 10f),
        };
        var bar = new ProgressBar { Left = 24, Top = 82, Width = 396, Height = 14, Style = ProgressBarStyle.Marquee };
        form.Controls.Add(label);
        form.Controls.Add(bar);
        var thread = new Thread(() =>
        {
            try { work(); }
            finally { form.BeginInvoke(new Action(form.Close)); }
        });
        form.Shown += (s, e) => thread.Start();
        Application.Run(form);
    }
}
