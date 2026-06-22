# 照片分割器 Demo

用于把扫描件、相册拼版、多张照片同页图片自动分割为独立 JPG。当前主界面为 Vue 桌面 UI，提供批量处理和单独处理两个工作流。

## 运行

- 本地源码运行：确认 Python 3.13+ 环境可用后运行 `run_gui.bat`。
- 发布版运行：使用 releases 中编译好的程序。
- 运行失败时，把错误日志交给 CODEX 检查。

## 发布版本

- `photo_splitter_v5.exe`：标准版，包含 OpenCV 和 YuNet 人脸检测模型。可使用 OpenCV OpenCL/T-API 或 OpenCV CPU 多线程，适合大多数电脑。
- `photo_splitter_v5_cpu.exe`：轻量 CPU 版，不包含 OpenCV。可以打开界面、读取图片并执行基础分割，但边缘检测、轮廓分析和人脸方向判断弱于标准版。

当前发布只保留标准版与轻量 CPU 版，不再维护单独的 CUDA 发布包。

## 功能

- 批量处理：递归扫描 JPG / JPEG / PNG / TIF / TIFF，保留子目录结构输出。
- 单独处理：单张图片检测预览，可新增、删除、拖动、缩放、二等分检测框，再确认导出。
- 输出核对：生成分割后的 JPG 和可选分割预览图，不额外保存 JSON 报告文件。
- Vue UI：包含批量处理、单独处理、参数区、预览区和日志区。
- 分割策略：通用平衡、积极分割、保守分割会影响边界候选、内部拆分、碎片过滤和兜底恢复策略。

![分割示例](./分割示例.jpg)

## 输出规则

- 子文件夹内图片保留原子目录样式，例如 `测试照片/08/0807.tif` 输出到 `输出/08/`。
- 根目录散图输出到源文件名文件夹，例如 `测试照片/Page0001.tif` 输出到 `输出/Page0001/`。
- 输出文件名格式为 `源文件名_001.jpg`、`源文件名_002.jpg`；预览图为 `分割预览_源文件名.jpg`。

## 后端说明

当前正式版本按以下顺序选择处理后端：

1. `opencv-opencl`：OpenCV OpenCL/T-API 可用时优先启用。
2. `opencv-cpu`：默认标准路线，使用 OpenCV CPU 多线程处理灰度、边缘和形态学步骤。
3. `numpy-cpu`：无 OpenCV 时的 NumPy/Pillow 后备路线。

界面会在启动时检测可用算力并显示检测/导出并发数量。GPU 名称会作为硬件信息展示；实际后端以界面显示的 `opencv-opencl`、`opencv-cpu` 或 `numpy-cpu` 为准。

仍主要由 CPU 执行的部分：PIL/TIFF 读取、`cv2.findContours` 轮廓提取、轮廓后几何合并、检测框规则判断、PIL 裁切和 JPEG 编码保存。

## 性能配置

- `PHOTO_SPLITTER_DETECT_WORKERS`：覆盖批量检测 worker 数。
- `PHOTO_SPLITTER_EXPORT_WORKERS`：覆盖批量导出 worker 数。
- `PHOTO_SPLITTER_DISABLE_OPENCL=1`：禁用 OpenCV OpenCL/T-API，强制走 OpenCV CPU 或后备路线。

默认检测 worker 会限制在 2-6 个，导出 worker 会限制在 2-8 个，并按 worker 数配置 OpenCV 内部线程，避免 CPU 线程过度抢占。当前导出统一使用快速 JPEG 保存，不启用 `optimize/progressive`。

## 模块结构

```text
photo_splitter/
  config.py           参数、预设、底色模式、文件格式配置
  runtime_backend.py  OpenCV/OpenCL/CPU 后端检测和像素预处理
  performance.py      worker 数、OpenCV 线程和 JPEG 保存参数
  io_utils.py         图片扫描、多页 TIFF 读取、输出命名、跨平台打开路径
  geometry.py         几何辅助函数
  detection.py        边界优先检测、网格识别、连通域、碎片合并
  postprocess.py      底色判断、裁白边、去邻图窄条、小角度纠偏、人脸方向旋转
  processing.py       CLI/GUI 共用的批量保存流程
  visualization.py    预览框绘制
  cli.py              命令行入口
  web_app.py          Vue 桌面 UI 的 Flask/pywebview 后端
  assets/             程序图标和预览图
  web_ui/             Vue 前端源码和 dist 编译产物
  build_demo_versioned.ps1
```

## 开发署名

设计开发：YY

代码完成：CODEX
