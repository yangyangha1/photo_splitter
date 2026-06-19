# 照片分割器 Demo

用于把扫描件、相册拼版、多张照片同页图片自动分割为独立 JPG。当前版本已拆分为 `photo_splitter/` 模块，主界面已重构为 Vue 桌面 UI，并提供批量处理和单独处理两个页面。

## 运行

双击：

```bat
run_gui.bat
```

或命令行运行 Vue 桌面 UI：

```bat
python -m photo_splitter.web_app
```

CLI 批量处理：

```bat
python -m photo_splitter.cli 测试照片 -o 输出 --preset balanced --background-mode auto --preview
```

## 功能

- 批量处理：递归扫描 JPG / JPEG / TIF / TIFF，保留子目录结构输出。
- 单独处理：单张图片检测预览，可新增、删除、拖动、缩放、二等分检测框，再确认导出。
- 单图缩放：单独处理预览区支持鼠标滚轮放大/缩小，检测框会随预览比例同步显示，便于微调。
- 输出核对：生成分割后的 JPG、分割预览图和 `split_report.json`。
- Vue UI：顶部两段式胶囊切换批量处理和单独处理，左右设置区、预览区和日志区严格对齐。
- 窗口操作：自定义顶部栏支持拖动窗口，双击顶部空白区域可最大化/还原，窗口边缘允许按 Windows 常规方式调整大小。
- 参数控件：处理配置预设、源图底色、人脸判断自动旋转使用 iOS 风格胶囊分段控件；边界阈值、白边阈值、倾斜矫正敏感度保留连续滑块。
- 程序内弹窗：完成、错误和提示都使用主窗口内遮罩弹窗，弹窗关闭前其它按钮不可点击。

## 输出规则

- 子文件夹内图片保持原子目录样式，例如 `测试照片/08/0807.tif` 输出到 `输出/08/`。
- 根目录散图按当前需求输出到源文件名文件夹，例如 `测试照片/Page0001.tif` 输出到 `输出/Page0001/`。
- 输出文件名格式为 `源文件名_001.jpg`、`源文件名_002.jpg`；预览图为 `分割预览_源文件名.jpg`。

## 参数说明

处理配置预设：一组面向常见场景的参数包，会同时调整边界阈值、最小照片面积、裁白边、源图底色和倾斜矫正敏感度。

源图底色：只描述原始扫描底板或相册背景颜色，用来覆盖或细化预设里的底色判断；它不是完整参数包，所以不等同于处理配置预设。

人脸判断自动旋转：导出阶段用 OpenCV 多级联人脸检测、眼睛验证、面积/位置/宽高比和四方向分数差综合判断 0/90/180/270 度方向；检测不到可靠人脸或分数不够明确时不会旋转。

检测边界阈值：控制暗边、相框和分割线参与检测的强度；数值越高越容易保留暗部内容，数值越低越容易过滤暗边。

裁切白边阈值：控制输出单张照片时裁掉白边的积极程度；数值越低裁切越积极，数值越高越保守。

倾斜矫正敏感度：控制小角度纠偏的触发门槛；百分比越低越积极，越高越稳。

## GPU 说明

当前代码会检测 CuPy CUDA、OpenCV CUDA、OpenCV OpenCL、OpenCV CPU 和 NumPy CPU 后端。本机当前 OpenCV 为 OpenCL 后端，可用于灰度、模糊、Canny 边缘和部分形态学处理；普通 `opencv-python-headless` 通常不包含 CUDA。

可以 GPU/硬件后端加速的部分：灰度/通道差预处理、背景差异计算、Canny 边缘、部分 OpenCV 形态学操作、部分 CUDA/OpenCL 图像算子。

仍然主要由 CPU 执行的部分：PIL/TIFF 读取、`cv2.findContours` 轮廓提取、轮廓后几何合并、检测框规则判断、PIL 裁切和 JPEG 编码保存。要让 NVIDIA CUDA 长期高占用，需要安装 CuPy 或 CUDA 编译版 OpenCV；当前 demo 版优先使用体积更可控的 OpenCV/OpenCL。

## 轻量依赖

推荐使用 `requirements.txt`，它包含 OpenCV，检测精度、速度、人脸方向判断和边缘/形态学处理更完整。

如果只需要更小依赖集合，可以使用：

```bat
pip install -r photo_splitter\requirements-no-opencv.txt
```

无 OpenCV 环境会自动降级到 NumPy/Pillow 后备路径。轻量版可以打开界面、读取图片、做基础预处理和部分分割，但边缘检测、轮廓分析、人脸自动旋转和复杂拼图识别能力弱于 OpenCV 版。

## 模块结构

```text
photo_splitter/
  config.py           参数、预设、底色模式、文件格式配置
  runtime_backend.py  CuPy/OpenCV/OpenCL/CPU 后端检测和像素预处理
  io_utils.py         图片扫描、多页 TIFF 读取、输出命名、跨平台打开路径
  geometry.py         几何辅助函数
  detection.py        边界优先检测、网格识别、连通域、碎片合并
  postprocess.py      底色判断、裁白边、去邻图窄条、小角度纠偏、人脸方向旋转
  processing.py       CLI/GUI 共用的批量保存流程
  visualization.py    预览框绘制
  cli.py              命令行入口
  web_app.py          Vue 桌面 UI 的 Flask/pywebview 后端
  assets/             程序实际使用的图标和预览图
  icon_source/        原始图标源文件备份
  web_ui/             Vue 前端源码
  web_static/         Vue 编译后的静态文件
  build_demo_versioned.ps1
  requirements.txt
  requirements-no-opencv.txt
```

## Vue UI 开发

首次安装依赖并编译：

```bat
cd photo_splitter\web_ui
npm install
npm run build
cd ..\..
```

开发调试时先启动 Python 后端，再启动 Vite：

```bat
python -m photo_splitter.web_app
cd photo_splitter\web_ui
npm run dev
```

## 打包 Demo EXE

已使用 `photo_splitter/icon_source/` 文件夹里的最新版图标同步生成 `photo_splitter/assets/photo_splitter_icon.ico`，并保留透明底、多尺寸图层和右下角下载箭头。当前 demo 使用单文件打包，默认保留 OpenCV 以支持 OpenCL 加速和人脸方向检测，排除 CuPy/Torch、tkinter/Tcl 和其它不用的 GUI 后端等大体积可选库。

重新打包使用版本化脚本，脚本会自动编译 Vue，并生成递增文件名。默认生成带 OpenCV 版本：

```bat
powershell -ExecutionPolicy Bypass -File photo_splitter\build_demo_versioned.ps1
```

生成无 OpenCV 轻量版本：

```bat
powershell -ExecutionPolicy Bypass -File photo_splitter\build_demo_versioned.ps1 -Variant no-opencv
```

一次生成带 OpenCV 和无 OpenCV 两个版本：

```bat
powershell -ExecutionPolicy Bypass -File photo_splitter\build_demo_versioned.ps1 -Variant all
```

生成文件位于：

```text
dist/photo_splitter_demo_v7_opencv.exe
dist/photo_splitter_demo_v7_no_opencv.exe
```

如果需要 NVIDIA CUDA 级别加速，需要额外安装 CuPy 或 CUDA 编译版 OpenCV 后再打包；这会明显增加依赖和程序体积。

## GitHub 发布

当前仓库已整理为源代码集中在 `photo_splitter/` 内、根目录只保留启动 bat、README 和测试/归档/输出等本地目录的结构；测试照片、输出目录、归档目录、`dist/` 和 `build/` 默认不进入 Git。

首次发布推荐流程：

```bat
winget install --id GitHub.cli
gh auth login
gh repo create photo_splitter --private --source . --remote origin --push
```

如果远程仓库已存在：

```bat
git remote add origin https://github.com/<你的账号>/photo_splitter.git
git push -u origin main
```

## 开发署名

设计开发：YY

代码完成：CODEX
