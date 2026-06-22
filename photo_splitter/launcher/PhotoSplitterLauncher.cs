using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal static class PhotoSplitterLauncher
{
    public const string PayloadResourceName = "PhotoSplitterPayload";
    public const string IconResourceName = "PhotoSplitterIconPreview";

    [STAThread]
    private static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new SplashForm(args));
    }

    public static string ResolveTarget(string[] args)
    {
        string baseDir = AppDomain.CurrentDomain.BaseDirectory;

        if (HasEmbeddedPayload())
        {
            return ExtractEmbeddedPayload();
        }

        if (args.Length > 0 && !string.IsNullOrWhiteSpace(args[0]))
        {
            string requested = args[0];
            return Path.IsPathRooted(requested) ? requested : Path.Combine(baseDir, requested);
        }

        string selfName = Path.GetFileName(Application.ExecutablePath);
        string[] patterns =
        {
            "photo_splitter_v*.exe",
            "photo_splitter_demo_v*.exe",
        };

        return patterns
            .SelectMany(pattern => Directory.GetFiles(baseDir, pattern))
            .Where(path => !string.Equals(Path.GetFileName(path), selfName, StringComparison.OrdinalIgnoreCase))
            .Select(path => new FileInfo(path))
            .OrderByDescending(file => file.LastWriteTimeUtc)
            .Select(file => file.FullName)
            .FirstOrDefault() ?? string.Empty;
    }

    public static string ResolveArguments(string[] args)
    {
        if (HasEmbeddedPayload())
        {
            return string.Join(" ", args.Select(QuoteArgument));
        }

        if (args.Length <= 1)
        {
            return string.Empty;
        }

        return string.Join(" ", args.Skip(1).Select(QuoteArgument));
    }

    public static string ResolveWorkingDirectory(string targetPath)
    {
        string requested = Environment.GetEnvironmentVariable("PHOTO_SPLITTER_WORKDIR");
        if (!string.IsNullOrWhiteSpace(requested) && Directory.Exists(requested))
        {
            return requested;
        }
        return Path.GetDirectoryName(targetPath) ?? AppDomain.CurrentDomain.BaseDirectory;
    }

    private static string QuoteArgument(string value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return "\"\"";
        }
        if (value.IndexOfAny(new[] { ' ', '\t', '\n', '\r', '"' }) < 0)
        {
            return value;
        }
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    private static bool HasEmbeddedPayload()
    {
        return Assembly.GetExecutingAssembly().GetManifestResourceNames().Contains(PayloadResourceName);
    }

    private static string ExtractEmbeddedPayload()
    {
        Assembly assembly = Assembly.GetExecutingAssembly();
        using (Stream source = assembly.GetManifestResourceStream(PayloadResourceName))
        {
            if (source == null)
            {
                return string.Empty;
            }

            string cacheRoot = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "PhotoSplitter",
                "Payloads"
            );
            Directory.CreateDirectory(cacheRoot);

            FileInfo self = new FileInfo(Application.ExecutablePath);
            string payloadName = string.Format(
                "photo_splitter_payload_{0}_{1}.exe",
                self.LastWriteTimeUtc.Ticks,
                source.Length
            );
            string target = Path.Combine(cacheRoot, payloadName);
            if (File.Exists(target) && new FileInfo(target).Length == source.Length)
            {
                return target;
            }

            string temp = target + ".tmp";
            if (File.Exists(temp))
            {
                File.Delete(temp);
            }

            using (FileStream output = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                source.CopyTo(output);
            }

            if (File.Exists(target))
            {
                File.Delete(target);
            }
            File.Move(temp, target);
            return target;
        }
    }
}

internal sealed class SplashForm : Form
{
    private readonly string[] _args;
    private readonly System.Windows.Forms.Timer _timer;
    private readonly Stopwatch _clock = Stopwatch.StartNew();
    private string _targetPath = string.Empty;
    private Process _process;
    private DateTime _targetStartedAt;
    private string _status = "正在启动主程序...";
    private Image _iconImage;

    public SplashForm(string[] args)
    {
        _args = args ?? new string[0];
        _iconImage = LoadIconImage();
        Width = 560;
        Height = 320;
        MinimumSize = Size;
        MaximumSize = Size;
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.None;
        AutoScroll = false;
        AutoScrollMinSize = Size.Empty;
        AutoSize = false;
        ControlBox = false;
        MaximizeBox = false;
        MinimizeBox = false;
        SizeGripStyle = SizeGripStyle.Hide;
        ShowInTaskbar = true;
        TopMost = true;
        BackColor = Color.FromArgb(11, 16, 24);
        DoubleBuffered = true;
        Text = "照片分割器启动中";
        HorizontalScroll.Enabled = false;
        HorizontalScroll.Visible = false;
        HorizontalScroll.Maximum = 0;
        VerticalScroll.Enabled = false;
        VerticalScroll.Visible = false;
        VerticalScroll.Maximum = 0;
        ApplyRoundedRegion();

        try
        {
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        }
        catch
        {
            // 图标读取失败不影响启动器显示。
        }

        _timer = new System.Windows.Forms.Timer { Interval = 33 };
        _timer.Tick += OnTick;
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            if (_timer != null)
            {
                _timer.Dispose();
            }
            if (_iconImage != null)
            {
                _iconImage.Dispose();
                _iconImage = null;
            }
        }
        base.Dispose(disposing);
    }

    protected override CreateParams CreateParams
    {
        get
        {
            const int WS_HSCROLL = 0x00100000;
            const int WS_VSCROLL = 0x00200000;
            CreateParams cp = base.CreateParams;
            cp.Style &= ~(WS_HSCROLL | WS_VSCROLL);
            return cp;
        }
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        HideSystemScrollBars();
    }

    protected override void OnShown(EventArgs e)
    {
        base.OnShown(e);
        ThreadPool.QueueUserWorkItem(_ => StartTarget());
        _timer.Start();
    }

    protected override void OnSizeChanged(EventArgs e)
    {
        base.OnSizeChanged(e);
        ApplyRoundedRegion();
    }

    protected override void OnMouseDown(MouseEventArgs e)
    {
        base.OnMouseDown(e);
        if (e.Button == MouseButtons.Left)
        {
            ReleaseCapture();
            SendMessage(Handle, 0xA1, new IntPtr(2), IntPtr.Zero);
        }
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        Graphics g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        HideSystemScrollBars();
        g.Clear(Color.Transparent);

        Rectangle outer = new Rectangle(0, 0, Width - 1, Height - 1);
        using (GraphicsPath outerPath = Rounded(outer, 8))
        using (LinearGradientBrush bg = new LinearGradientBrush(outer, Color.FromArgb(13, 21, 32), Color.FromArgb(7, 11, 17), 135f))
        using (Pen outerStroke = new Pen(Color.FromArgb(55, 255, 255, 255), 1))
        {
            g.FillPath(bg, outerPath);
            g.DrawPath(outerStroke, outerPath);
        }

        Rectangle panel = new Rectangle(28, 26, Width - 56, Height - 52);
        using (GraphicsPath panelPath = Rounded(panel, 8))
        using (SolidBrush panelBrush = new SolidBrush(Color.FromArgb(190, 18, 24, 34)))
        using (Pen panelStroke = new Pen(Color.FromArgb(42, 255, 255, 255), 1))
        {
            g.FillPath(panelBrush, panelPath);
            g.DrawPath(panelStroke, panelPath);
        }

        DrawIcon(g, panel.Left + 34, panel.Top + 34);
        DrawText(g, panel);
    }

    private void StartTarget()
    {
        try
        {
            SetStatus("正在准备主程序...");
            _targetPath = PhotoSplitterLauncher.ResolveTarget(_args);
            if (string.IsNullOrWhiteSpace(_targetPath) || !File.Exists(_targetPath))
            {
                SetStatus("没有找到照片分割器主程序。");
                return;
            }

            SetStatus("正在启动主程序...");
            _targetStartedAt = DateTime.Now;
            ProcessStartInfo info = new ProcessStartInfo
            {
                FileName = _targetPath,
                Arguments = PhotoSplitterLauncher.ResolveArguments(_args),
                WorkingDirectory = PhotoSplitterLauncher.ResolveWorkingDirectory(_targetPath),
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            };
            _process = Process.Start(info);
        }
        catch (Exception ex)
        {
            SetStatus("启动失败：" + ex.Message);
        }
    }

    private void ApplyRoundedRegion()
    {
        using (GraphicsPath path = Rounded(new Rectangle(0, 0, Width, Height), 8))
        {
            Region oldRegion = Region;
            Region = new Region(path);
            if (oldRegion != null)
            {
                oldRegion.Dispose();
            }
        }
    }

    private void HideSystemScrollBars()
    {
        if (!IsHandleCreated) return;
        ShowScrollBar(Handle, 3, false);
    }

    private void SetStatus(string value)
    {
        _status = value;
        try
        {
            if (IsHandleCreated)
            {
                BeginInvoke(new Action(Invalidate));
            }
        }
        catch
        {
            // 窗口关闭期间可能无法投递刷新，忽略即可。
        }
    }

    private void OnTick(object sender, EventArgs e)
    {
        if (_process != null)
        {
            try
            {
                _process.Refresh();
                if (_process.HasExited)
                {
                    _status = "主程序已退出，请重新打开。";
                }
            }
            catch
            {
                // 进程刷新失败时继续保留启动提示。
            }
        }

        if (_clock.ElapsedMilliseconds > 1200 && HasVisibleMainWindow())
        {
            Close();
            return;
        }

        if (_clock.ElapsedMilliseconds > 12000 && _status == "正在启动主程序...")
        {
            _status = "主程序仍在加载，请稍等...";
        }

        Invalidate();
    }

    private void DrawIcon(Graphics g, int x, int y)
    {
        Rectangle target = new Rectangle(x, y, 48, 48);
        if (_iconImage != null)
        {
            InterpolationMode oldInterpolation = g.InterpolationMode;
            PixelOffsetMode oldPixelOffset = g.PixelOffsetMode;
            CompositingQuality oldCompositing = g.CompositingQuality;
            try
            {
                g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                g.PixelOffsetMode = PixelOffsetMode.HighQuality;
                g.CompositingQuality = CompositingQuality.HighQuality;
                g.DrawImage(_iconImage, target);
                return;
            }
            finally
            {
                g.InterpolationMode = oldInterpolation;
                g.PixelOffsetMode = oldPixelOffset;
                g.CompositingQuality = oldCompositing;
            }
        }

        try
        {
            using (Icon icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath))
            {
                g.DrawIcon(icon, target);
            }
        }
        catch
        {
            using (SolidBrush brush = new SolidBrush(Color.FromArgb(61, 155, 255)))
            {
                g.FillEllipse(brush, x, y, 48, 48);
            }
        }
    }

    private static Image LoadIconImage()
    {
        try
        {
            Assembly assembly = Assembly.GetExecutingAssembly();
            using (Stream source = assembly.GetManifestResourceStream(PhotoSplitterLauncher.IconResourceName))
            {
                if (source == null)
                {
                    return null;
                }
                using (Image loaded = Image.FromStream(source))
                {
                    return new Bitmap(loaded);
                }
            }
        }
        catch
        {
            return null;
        }
    }

    private void DrawText(Graphics g, Rectangle panel)
    {
        using (Font title = new Font("Microsoft YaHei UI", 22f, FontStyle.Bold))
        using (Font body = new Font("Microsoft YaHei UI", 10.5f, FontStyle.Regular))
        using (SolidBrush titleBrush = new SolidBrush(Color.FromArgb(238, 244, 252)))
        using (SolidBrush bodyBrush = new SolidBrush(Color.FromArgb(158, 171, 188)))
        {
            g.DrawString("照片分割器", title, titleBrush, panel.Left + 96, panel.Top + 32);
            g.DrawString(_status, body, bodyBrush, panel.Left + 98, panel.Top + 75);
            g.DrawString("首次打开需要解压主程序和等待系统安全扫描。", body, bodyBrush, panel.Left + 34, panel.Top + 148);
            g.DrawString("设计开发：YY    代码完成：CODEX", body, bodyBrush, panel.Left + 34, panel.Top + 184);
        }
    }

    private static GraphicsPath Rounded(Rectangle rect, int radius)
    {
        int d = Math.Min(Math.Min(rect.Width, rect.Height), radius * 2);
        GraphicsPath path = new GraphicsPath();
        path.AddArc(rect.X, rect.Y, d, d, 180, 90);
        path.AddArc(rect.Right - d, rect.Y, d, d, 270, 90);
        path.AddArc(rect.Right - d, rect.Bottom - d, d, d, 0, 90);
        path.AddArc(rect.X, rect.Bottom - d, d, d, 90, 90);
        path.CloseFigure();
        return path;
    }

    private bool HasVisibleMainWindow()
    {
        bool found = false;
        EnumWindows((handle, lParam) =>
        {
            if (handle == Handle || !IsWindowVisible(handle))
            {
                return true;
            }

            StringBuilder title = new StringBuilder(256);
            GetWindowText(handle, title, title.Capacity);
            if (title.ToString().Contains("照片分割器"))
            {
                uint windowPid;
                GetWindowThreadProcessId(handle, out windowPid);
                if (IsNewTargetWindow((int)windowPid))
                {
                    found = true;
                    return false;
                }
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    private bool IsNewTargetWindow(int processId)
    {
        try
        {
            using (Process process = Process.GetProcessById(processId))
            {
                return process.StartTime >= _targetStartedAt.AddSeconds(-3);
            }
        }
        catch
        {
            return false;
        }
    }

    [DllImport("user32.dll")]
    private static extern bool ReleaseCapture();

    [DllImport("user32.dll")]
    private static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

    [DllImport("user32.dll")]
    private static extern bool ShowScrollBar(IntPtr hWnd, int wBar, bool bShow);

    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc enumProc, IntPtr lParam);
}
