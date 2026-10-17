// NPU 翻译 —— 原生安装器（.NET WinForms，单文件）
//
// 为什么不用 Inno Setup：部分机器上的安全软件会拦截安装器「先解压到 %TEMP%\is-*.tmp」
// 这个行为，直接返回 ACCESS_DENIED，导致安装器启动即失败（即使以管理员运行）。
// 本安装器把整包 zip 直接附加在自身 exe 末尾，安装时流式解压到用户选择的目录，
// 全程不使用临时目录，也不做任何自解压，从根上避开这类拦截。
//
// 编译：csc.exe /target:winexe /out:NPU安装程序.exe /r:System.Windows.Forms.dll /r:System.Drawing.dll
//                /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll installer_native.cs
// 打包：build_native_installer.ps1 负责把 dist 压成 zip 并附加到 exe 后面。

using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace NpuTranslateSetup
{
    /// <summary>把文件的一段区间包装成可随机访问的流，供 ZipArchive 直接读取。</summary>
    internal sealed class RangeStream : Stream
    {
        private readonly Stream _base;
        private readonly long _start;
        private readonly long _length;
        private long _pos;

        public RangeStream(Stream baseStream, long start, long length)
        {
            _base = baseStream;
            _start = start;
            _length = length;
            _pos = 0;
        }

        public override bool CanRead { get { return true; } }
        public override bool CanSeek { get { return true; } }
        public override bool CanWrite { get { return false; } }
        public override long Length { get { return _length; } }
        public override long Position
        {
            get { return _pos; }
            set { _pos = value; }
        }

        public override int Read(byte[] buffer, int offset, int count)
        {
            long remain = _length - _pos;
            if (remain <= 0) return 0;
            if (count > remain) count = (int)remain;
            _base.Position = _start + _pos;
            int n = _base.Read(buffer, offset, count);
            _pos += n;
            return n;
        }

        public override long Seek(long offset, SeekOrigin origin)
        {
            long target = offset;
            if (origin == SeekOrigin.Current) target = _pos + offset;
            else if (origin == SeekOrigin.End) target = _length + offset;
            _pos = target;
            return _pos;
        }

        public override void Flush() { }
        public override void SetLength(long value) { throw new NotSupportedException(); }
        public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
    }

    internal static class Payload
    {
        // 尾部布局：[zip 数据][8 字节 offset(小端)][8 字节 length(小端)][8 字节魔数]
        private static readonly byte[] Magic = Encoding.ASCII.GetBytes("NPUSETUP");

        /// <summary>读取尾部信息，返回 [数据偏移, 数据长度]。</summary>
        public static void ReadTrailer(out long offset, out long length)
        {
            string self = Assembly.GetExecutingAssembly().Location;
            using (var f = new FileStream(self, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                long total = f.Length;
                if (total < 24) throw new InvalidDataException("安装包不完整");
                f.Position = total - 24;
                byte[] trailer = new byte[24];
                f.Read(trailer, 0, 24);
                for (int i = 0; i < 8; i++)
                {
                    if (trailer[16 + i] != Magic[i])
                        throw new InvalidDataException("安装包格式不正确（缺少数据尾部）");
                }
                offset = BitConverter.ToInt64(trailer, 0);
                length = BitConverter.ToInt64(trailer, 8);
                if (offset <= 0 || length <= 0 || offset + length > total)
                    throw new InvalidDataException("安装包数据损坏");
            }
        }

        /// <summary>
        /// 把「安装器外壳」（不含 352MB 数据）写成卸载程序。
        /// 卸载逻辑只用到自身路径，不需要数据段，所以 18 KB 就够了 —— 
        /// 直接整包复制会让安装目录凭空多出 352 MB。
        /// </summary>
        public static void WriteUninstallerTo(string destPath)
        {
            long offset, length;
            ReadTrailer(out offset, out length);
            string self = Assembly.GetExecutingAssembly().Location;
            if (string.Equals(self, destPath, StringComparison.OrdinalIgnoreCase)) return;
            using (var src = new FileStream(self, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var dst = new FileStream(destPath, FileMode.Create, FileAccess.Write, FileShare.None))
            {
                byte[] buf = new byte[1 << 16];
                long remain = offset;
                while (remain > 0)
                {
                    int want = (int)Math.Min(buf.Length, remain);
                    int n = src.Read(buf, 0, want);
                    if (n <= 0) break;
                    dst.Write(buf, 0, n);
                    remain -= n;
                }
            }
        }

        public static ZipArchive Open(out Stream fileStream)
        {
            string self = Assembly.GetExecutingAssembly().Location;
            fileStream = new FileStream(self, FileMode.Open, FileAccess.Read, FileShare.Read);
            long offset, length;
            ReadTrailer(out offset, out length);
            var range = new RangeStream(fileStream, offset, length);
            return new ZipArchive(range, ZipArchiveMode.Read, false);
        }
    }

    internal sealed class SetupForm : Form
    {
        private readonly TextBox _path = new TextBox();
        private readonly CheckBox _desktop = new CheckBox();
        private readonly CheckBox _autostart = new CheckBox();
        private readonly Button _browse = new Button();
        private readonly Button _install = new Button();
        private readonly Button _cancel = new Button();
        private readonly ProgressBar _bar = new ProgressBar();
        private readonly Label _status = new Label();
        private readonly TextBox _log = new TextBox();
        private volatile bool _working;

        private static readonly string AppName = "NPU 翻译";

        public SetupForm()
        {
            Text = AppName + " 安装程序";
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(620, 430);
            Font = new Font("Microsoft YaHei UI", 9f);

            var title = new Label
            {
                Text = "安装 " + AppName,
                Font = new Font("Microsoft YaHei UI", 15f, FontStyle.Bold),
                Location = new Point(24, 20),
                AutoSize = true
            };
            Controls.Add(title);

            var sub = new Label
            {
                Text = "选择安装位置后点击「开始安装」。安装包已内置 FP16 翻译模型，装完即可使用。",
                ForeColor = Color.DimGray,
                Location = new Point(26, 58),
                AutoSize = true
            };
            Controls.Add(sub);

            Controls.Add(new Label { Text = "安装目录：", Location = new Point(26, 100), AutoSize = true });

            _path.Location = new Point(110, 97);
            _path.Width = 390;
            _path.Text = DefaultDir();
            Controls.Add(_path);

            _browse.Text = "浏览…";
            _browse.Location = new Point(508, 95);
            _browse.Width = 84;
            _browse.Click += OnBrowse;
            Controls.Add(_browse);

            _desktop.Text = "创建桌面快捷方式";
            _desktop.Checked = true;
            _desktop.Location = new Point(110, 130);
            _desktop.AutoSize = true;
            Controls.Add(_desktop);

            _autostart.Text = "开机自动启动翻译服务（后台常驻）";
            _autostart.Location = new Point(110, 156);
            _autostart.AutoSize = true;
            Controls.Add(_autostart);

            _bar.Location = new Point(26, 195);
            _bar.Width = 566;
            _bar.Height = 18;
            Controls.Add(_bar);

            _status.Text = "准备就绪";
            _status.ForeColor = Color.DimGray;
            _status.Location = new Point(26, 218);
            _status.AutoSize = true;
            Controls.Add(_status);

            _log.Location = new Point(26, 244);
            _log.Multiline = true;
            _log.ReadOnly = true;
            _log.ScrollBars = ScrollBars.Vertical;
            _log.Width = 566;
            _log.Height = 120;
            _log.BackColor = Color.FromArgb(250, 250, 250);
            _log.Font = new Font("Consolas", 8.5f);
            Controls.Add(_log);

            _install.Text = "开始安装";
            _install.Location = new Point(392, 380);
            _install.Width = 100;
            _install.Height = 32;
            _install.Click += OnInstall;
            Controls.Add(_install);

            _cancel.Text = "退出";
            _cancel.Location = new Point(502, 380);
            _cancel.Width = 90;
            _cancel.Height = 32;
            _cancel.Click += delegate { Close(); };
            Controls.Add(_cancel);
        }

        private static string DefaultDir()
        {
            string baseDir = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (string.IsNullOrEmpty(baseDir)) baseDir = @"C:\";
            return Path.Combine(baseDir, @"Programs\" + AppName);
        }

        private void OnBrowse(object sender, EventArgs e)
        {
            using (var dlg = new FolderBrowserDialog())
            {
                dlg.Description = "选择 " + AppName + " 的安装目录";
                dlg.ShowNewFolderButton = true;
                try
                {
                    if (Directory.Exists(_path.Text)) dlg.SelectedPath = _path.Text;
                }
                catch { }
                if (dlg.ShowDialog(this) == DialogResult.OK) _path.Text = dlg.SelectedPath;
            }
        }

        private void Log(string s)
        {
            if (InvokeRequired) { BeginInvoke(new Action<string>(Log), s); return; }
            _log.AppendText(s + Environment.NewLine);
        }

        private void SetStatus(string s, int percent)
        {
            if (InvokeRequired)
            {
                BeginInvoke(new Action<string, int>(SetStatus), s, percent);
                return;
            }
            _status.Text = s;
            if (percent >= 0) _bar.Value = Math.Min(100, Math.Max(0, percent));
        }

        private void OnInstall(object sender, EventArgs e)
        {
            if (_working) return;
            string target = (_path.Text ?? "").Trim().Trim('"');
            if (target.Length == 0)
            {
                MessageBox.Show(this, "请先选择安装目录。", "提示", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            try
            {
                Directory.CreateDirectory(target);
                string probe = Path.Combine(target, ".npu_write_test");
                File.WriteAllText(probe, "ok");
                File.Delete(probe);
            }
            catch (Exception ex)
            {
                MessageBox.Show(this,
                    "该目录无法写入：" + target + "\r\n\r\n" + ex.Message +
                    "\r\n\r\n请换一个目录（例如 D:\\NPU翻译）。",
                    "目录不可写", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            _working = true;
            _install.Enabled = false;
            _browse.Enabled = false;
            _path.Enabled = false;
            _desktop.Enabled = false;
            _autostart.Enabled = false;
            _cancel.Enabled = false;
            _log.Clear();

            bool desktop = _desktop.Checked;
            bool autostart = _autostart.Checked;
            var t = new Thread(delegate () { RunInstall(target, desktop, autostart); });
            t.IsBackground = true;
            t.Start();
        }

        private void RunInstall(string target, bool desktop, bool autostart)
        {
            try
            {
                Log("安装目录: " + target);
                int files = ExtractAll(target);
                Log("已解压 " + files + " 个文件");
                SetStatus("创建快捷方式…", 100);

                string exe = Path.Combine(target, "NPU翻译.exe");
                var shortcuts = new List<string>();
                string menuDir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.Programs), AppName);
                TryCreateShortcut(Path.Combine(menuDir, AppName + ".lnk"), exe, target, shortcuts);
                TryCreateShortcut(Path.Combine(menuDir, "卸载 " + AppName + ".lnk"),
                                  Path.Combine(target, "uninstall-npu.exe"), target, shortcuts);
                if (desktop)
                {
                    string desktopDir = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                    TryCreateShortcut(Path.Combine(desktopDir, AppName + ".lnk"), exe, target, shortcuts);
                }

                WriteUninstaller(target);
                if (autostart) TrySetAutostart(Path.Combine(target, "启动服务.bat"));

                Log("完成。");
                SetStatus("安装完成", 100);
                if (InvokeRequired) BeginInvoke(new Action(Finish));
                else Finish();
            }
            catch (Exception ex)
            {
                Log("错误: " + ex);
                SetStatus("安装失败", 0);
                if (InvokeRequired)
                    BeginInvoke(new Action(delegate
                    {
                        MessageBox.Show(this, "安装失败：\r\n\r\n" + ex.Message, "错误",
                                        MessageBoxButtons.OK, MessageBoxIcon.Error);
                        _install.Enabled = true;
                        _browse.Enabled = true;
                        _path.Enabled = true;
                        _working = false;
                    }));
            }
        }

        private void Finish()
        {
            string tray = Path.Combine(_path.Text.Trim(), "NPU翻译.exe");
            if (MessageBox.Show(this,
                    "安装完成！\r\n\r\n是否立即启动 " + AppName + "？",
                    "安装完成", MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
            {
                try { System.Diagnostics.Process.Start(tray); } catch { }
            }
            Close();
        }

        /// <summary>静默安装（供自动化/脚本使用，无界面）。返回 0 表示成功。</summary>
        public static int SilentInstall(string target, bool desktop, bool autostart)
        {
            try
            {
                target = (target ?? "").Trim().Trim('"');
                if (target.Length == 0) return 2;
                Console.WriteLine("安装目录: " + target);
                Directory.CreateDirectory(target);
                string exe = Path.Combine(target, "NPU翻译.exe");

                int files = ExtractAllStatic(target, delegate (string s) { Console.WriteLine(s); });
                Console.WriteLine("已解压 " + files + " 个文件");

                string menuDir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.Programs), AppName);
                Shortcut.Create(Path.Combine(menuDir, AppName + ".lnk"), exe, target);
                Shortcut.Create(Path.Combine(menuDir, "卸载 " + AppName + ".lnk"),
                                Path.Combine(target, "uninstall-npu.exe"), target);
                if (desktop)
                {
                    string desktopDir = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                    Shortcut.Create(Path.Combine(desktopDir, AppName + ".lnk"), exe, target);
                }

                Payload.WriteUninstallerTo(Path.Combine(target, "uninstall-npu.exe"));

                if (autostart)
                {
                    try
                    {
                        using (var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                            @"Software\Microsoft\Windows\CurrentVersion\Run", true))
                        {
                            if (key != null) key.SetValue("NPUTranslate",
                                "\"" + Path.Combine(target, "启动服务.bat") + "\"");
                        }
                    }
                    catch (Exception ex) { Console.WriteLine("自启设置失败（忽略）: " + ex.Message); }
                }

                Console.WriteLine("安装完成");
                return 0;
            }
            catch (Exception ex)
            {
                Console.WriteLine("安装失败: " + ex);
                return 1;
            }
        }

        private static int ExtractAllStatic(string target, Action<string> log)
        {
            Stream fs;
            using (var zip = Payload.Open(out fs))
            {
                int total = zip.Entries.Count, done = 0;
                foreach (var entry in zip.Entries)
                {
                    done++;
                    string name = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                    string dest = Path.Combine(target, name);
                    if (name.EndsWith(Path.DirectorySeparatorChar.ToString()))
                    {
                        Directory.CreateDirectory(dest);
                        continue;
                    }
                    Directory.CreateDirectory(Path.GetDirectoryName(dest));
                    using (var src = entry.Open())
                    using (var dst = new FileStream(dest, FileMode.Create, FileAccess.Write,
                                                    FileShare.None, 1 << 20))
                    {
                        src.CopyTo(dst, 1 << 20);
                    }
                    if (done % 50 == 0 || done == total)
                        log("  解压 " + done + " / " + total);
                }
                return total;
            }
        }

        private int ExtractAll(string target)
        {
            Stream fs;
            using (var zip = Payload.Open(out fs))
            {
                int total = zip.Entries.Count, done = 0;
                foreach (var entry in zip.Entries)
                {
                    done++;
                    if (done % 20 == 0 || done == total)
                        SetStatus("正在解压 " + done + " / " + total, done * 100 / Math.Max(1, total));

                    string name = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                    string dest = Path.Combine(target, name);
                    if (name.EndsWith(Path.DirectorySeparatorChar.ToString()))
                    {
                        Directory.CreateDirectory(dest);
                        continue;
                    }
                    Directory.CreateDirectory(Path.GetDirectoryName(dest));
                    using (var src = entry.Open())
                    using (var dst = new FileStream(dest, FileMode.Create, FileAccess.Write,
                                                    FileShare.None, 1 << 20))
                    {
                        src.CopyTo(dst, 1 << 20);
                    }
                }
                return total;
            }
        }

        private void TryCreateShortcut(string lnkPath, string targetExe, string workDir,
                                        List<string> created)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(lnkPath));
                Type t = Type.GetTypeFromProgID("WScript.Shell");
                object shell = Activator.CreateInstance(t);
                object lnk = t.InvokeMember("CreateShortcut",
                    BindingFlags.InvokeMethod, null, shell, new object[] { lnkPath });
                Type lt = lnk.GetType();
                lt.InvokeMember("TargetPath", BindingFlags.SetProperty, null, lnk, new object[] { targetExe });
                lt.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, lnk, new object[] { workDir });
                lt.InvokeMember("IconLocation", BindingFlags.SetProperty, null, lnk,
                                new object[] { targetExe + ",0" });
                lt.InvokeMember("Save", BindingFlags.InvokeMethod, null, lnk, null);
                Log("  快捷方式: " + lnkPath);
            }
            catch (Exception ex)
            {
                Log("  快捷方式失败（忽略）: " + lnkPath + " — " + ex.Message);
            }
        }

        private void TrySetAutostart(string batPath)
        {
            try
            {
                using (var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                    @"Software\Microsoft\Windows\CurrentVersion\Run", true))
                {
                    if (key != null)
                    {
                        key.SetValue("NPUTranslate", "\"" + batPath + "\"");
                        Log("  已设置开机自启");
                    }
                }
            }
            catch (Exception ex)
            {
                Log("  开机自启失败（忽略）: " + ex.Message);
            }
        }

        /// <summary>生成卸载程序：复制自身并在旁边标记为卸载模式。</summary>
        private void WriteUninstaller(string target)
        {
            try
            {
                string dst = Path.Combine(target, "uninstall-npu.exe");
                Payload.WriteUninstallerTo(dst);
                Log("  卸载程序: " + dst);
            }
            catch (Exception ex)
            {
                Log("  卸载程序写入失败（忽略）: " + ex.Message);
            }
        }
    }

    /// <summary>通过 WScript.Shell 创建快捷方式（失败不抛，由调用方决定是否忽略）。</summary>
    internal static class Shortcut
    {
        public static bool Create(string lnkPath, string targetExe, string workDir)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(lnkPath));
                Type t = Type.GetTypeFromProgID("WScript.Shell");
                object shell = Activator.CreateInstance(t);
                object lnk = t.InvokeMember("CreateShortcut",
                    BindingFlags.InvokeMethod, null, shell, new object[] { lnkPath });
                Type lt = lnk.GetType();
                lt.InvokeMember("TargetPath", BindingFlags.SetProperty, null, lnk, new object[] { targetExe });
                lt.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, lnk, new object[] { workDir });
                lt.InvokeMember("IconLocation", BindingFlags.SetProperty, null, lnk,
                                new object[] { targetExe + ",0" });
                lt.InvokeMember("Save", BindingFlags.InvokeMethod, null, lnk, null);
                return true;
            }
            catch
            {
                return false;
            }
        }
    }

    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            // 以 uninstall-npu.exe 运行时执行卸载
            string exeName = Path.GetFileNameWithoutExtension(Assembly.GetExecutingAssembly().Location);
            if (string.Equals(exeName, "uninstall-npu", StringComparison.OrdinalIgnoreCase))
            {
                Uninstall.Run();
                return;
            }

            // 静默安装：NPU安装程序.exe --silent "D:\NPU翻译" [--desktop] [--autostart]
            if (args.Length >= 2 &&
                string.Equals(args[0], "--silent", StringComparison.OrdinalIgnoreCase))
            {
                bool desktop = false, autostart = false;
                for (int i = 2; i < args.Length; i++)
                {
                    if (string.Equals(args[i], "--desktop", StringComparison.OrdinalIgnoreCase)) desktop = true;
                    if (string.Equals(args[i], "--autostart", StringComparison.OrdinalIgnoreCase)) autostart = true;
                }
                int code = SetupForm.SilentInstall(args[1], desktop, autostart);
                Environment.Exit(code);
                return;
            }

            Application.Run(new SetupForm());
        }
    }

    internal static class Uninstall
    {
        public static void Run()
        {
            string dir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            if (MessageBox.Show(
                    "确定要卸载「NPU 翻译」吗？\r\n\r\n将删除：" + dir +
                    "\r\n（包括已下载的模型）",
                    "卸载 NPU 翻译", MessageBoxButtons.YesNo, MessageBoxIcon.Question)
                != DialogResult.Yes) return;

            try
            {
                foreach (var p in System.Diagnostics.Process.GetProcessesByName("NPU翻译"))
                    try { p.Kill(); } catch { }
                foreach (var p in System.Diagnostics.Process.GetProcessesByName("NPU翻译-服务"))
                    try { p.Kill(); } catch { }
                Thread.Sleep(600);
            }
            catch { }

            try
            {
                using (var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                    @"Software\Microsoft\Windows\CurrentVersion\Run", true))
                {
                    if (key != null) key.DeleteValue("NPUTranslate", false);
                }
            }
            catch { }

            try
            {
                string menuDir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.Programs), "NPU 翻译");
                if (Directory.Exists(menuDir)) Directory.Delete(menuDir, true);
                string desktopLnk = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "NPU 翻译.lnk");
                if (File.Exists(desktopLnk)) File.Delete(desktopLnk);
            }
            catch { }

            // 自身在目录里，先重启到临时脚本再删（这里用 cmd 延迟删除，避免文件占用）
            try
            {
                string cmd = "/c ping 127.0.0.1 -n 3 >nul & rd /s /q \"" + dir + "\"";
                var psi = new System.Diagnostics.ProcessStartInfo("cmd.exe", cmd)
                {
                    CreateNoWindow = true,
                    UseShellExecute = false,
                    WorkingDirectory = Path.GetTempPath()
                };
                System.Diagnostics.Process.Start(psi);
                MessageBox.Show("已卸载。", "NPU 翻译", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            catch (Exception ex)
            {
                MessageBox.Show("卸载时出错，请手动删除目录：\r\n" + dir + "\r\n\r\n" + ex.Message,
                                "错误", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }
}

