# 照片分割器 Demo

用于把扫描件、相册拼版、多张照片同页图片自动分割为独立 JPG。当前版本已拆分为 `photo_splitter/` 模块，主界面已重构为 Vue 桌面 UI，并提供批量处理和单独处理两个页面。

## 运行

- 确认具有python3.13以上版本和requirements列出依赖的环境，运行run_gui.bat
- 使用releases中编译的程序
- 如果运行失败，寻求CODEX帮助

## 发布版本

- `photo_splitter_v3.exe`：标准版，包含 OpenCV，适合大多数电脑。可使用 OpenCV OpenCL/T-API 或 OpenCV CPU 路径，检测精度和兼容性最均衡。
- `photo_splitter_v3_cpu.exe`：CPU 轻量版，不包含 OpenCV 和 CuPy，体积最小。可打开界面、读取图片并执行基础分割，但边缘检测、轮廓分析和人脸方向判断弱于标准版。
- `photo_splitter_v3_cupy_cuda.exe`：CuPy CUDA 版，面向本机或已安装 CUDA 12 runtime 的 NVIDIA 显卡电脑。程序不内置完整 CUDA 运行库；当系统 `CUDA_PATH` 或 PATH 中可找到 CUDA 12 DLL，例如 `cudart64_12.dll`、`cublas64_12.dll`，且 NVIDIA 驱动正常时，系统检测日志会显示 `CuPy CUDA GPU`。如果目标电脑缺少匹配 CUDA runtime，会自动降级到 OpenCV 或 CPU 后端。

## 功能

- 批量处理：递归扫描 JPG / JPEG / PNG / TIF / TIFF，保留子目录结构输出。
- 单独处理：单张图片检测预览，可新增、删除、拖动、缩放、二等分检测框，再确认导出。
- 单图缩放：单独处理预览区支持鼠标滚轮放大/缩小，检测框会随预览比例同步显示，便于微调。
- 输出核对：生成分割后的 JPG 和分割预览图，不再额外保存 JSON 报告文件。
- Vue UI：顶部两段式胶囊切换批量处理和单独处理，左右设置区、预览区和日志区严格对齐。
- 窗口操作：自定义顶部栏支持拖动窗口，双击顶部空白区域可最大化/还原，窗口边缘允许按 Windows 常规方式调整大小。
- 参数控件：处理配置预设、源图底色、人脸判断自动旋转使用 iOS 风格胶囊分段控件；边界阈值、白边阈值、倾斜矫正敏感度保留连续滑块。
- 程序内弹窗：完成、错误和提示都使用主窗口内遮罩弹窗，弹窗关闭前其它按钮不可点击。
- 分割策略：通用平衡、积极分割、保守分割不只调整参数，也会影响边界候选、内部拆分、碎片过滤和兜底恢复策略。

![分割示例](./分割示例.jpg)

## 输出规则

- 子文件夹内图片保持原子目录样式，例如 `测试照片/08/0807.tif` 输出到 `输出/08/`。
- 根目录散图按当前需求输出到源文件名文件夹，例如 `测试照片/Page0001.tif` 输出到 `输出/Page0001/`。
- 输出文件名格式为 `源文件名_001.jpg`、`源文件名_002.jpg`；预览图为 `分割预览_源文件名.jpg`。

## GPU 说明

当前代码会按 CuPy CUDA、OpenCV CUDA、OpenCV OpenCL、OpenCV CPU 和 NumPy CPU 的顺序检测并降级。标准版优先使用 OpenCV/OpenCL；CPU 版会走 NumPy/Pillow 后备路径；CuPy CUDA 版在系统 CUDA 12 runtime 可用时会走 CuPy CUDA。

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
  web_ui/             Vue 前端源码和 dist 编译产物
  build_demo_versioned.ps1
  requirements.txt
  requirements-no-opencv.txt
```

## 开发署名

设计开发：YY

代码完成：CODEX
