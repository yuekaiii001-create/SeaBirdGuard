import os
import shutil
import argparse
from pathlib import Path
from ultralytics import YOLO
import cv2

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif'}

def get_image_files(folder_path):
    image_files = []
    for root, _, files in os.walk(folder_path):
        for file in files:
            if Path(file).suffix.lower() in IMAGE_EXTENSIONS:
                image_files.append(Path(root) / file)
    return image_files

def get_max_confidence(model, image_path):
    try:
        results = model(image_path, verbose=False)
        if len(results) == 0:
            return 0.0
        result = results[0]
        if result.boxes is None or len(result.boxes) == 0:
            return 0.0
        confidences = result.boxes.conf.cpu().numpy()
        return float(confidences.max())
    except Exception as e:
        print(f"处理图片 {image_path} 时出错: {e}")
        return 0.0

def main():
    import sys
    if len(sys.argv) == 1:
        folder_input = input("请输入要处理的文件夹路径: ").strip().strip('"')
        output_input = input("请输入输出文件夹路径（直接回车使用默认位置）: ").strip().strip('"')
        sys.argv.extend([folder_input])
        if output_input:
            sys.argv.extend(['-o', output_input])

    parser = argparse.ArgumentParser(description="使用YOLOv8m筛选置信度前十的图片，并用置信度命名")
    parser.add_argument("folder", type=str, help="要处理的文件夹路径")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="输出文件夹路径（默认在原文件夹同级创建 'top10_confidence'）")
    parser.add_argument("--model", "-m", type=str, default="yolov8m.pt",
                        help="YOLO模型文件路径或名称（默认为 yolov8m.pt）")
    parser.add_argument("--top", "-k", type=int, default=10,
                        help="要复制的图片数量（默认 10）")
    args = parser.parse_args()

    input_folder = Path(args.folder).resolve()
    if not input_folder.exists() or not input_folder.is_dir():
        print(f"错误：文件夹 '{input_folder}' 不存在或不是有效目录。")
        return

    if args.output:
        output_folder = Path(args.output).resolve()
    else:
        output_folder = input_folder.parent / f"{input_folder.name}_top{args.top}_confidence"
    output_folder.mkdir(parents=True, exist_ok=True)

    print(f"正在加载模型 {args.model} ...")
    model = YOLO(args.model)

    print(f"正在扫描文件夹 {input_folder} 中的图片...")
    image_paths = get_image_files(input_folder)
    if not image_paths:
        print("未找到任何图片文件。")
        return
    print(f"找到 {len(image_paths)} 张图片，开始检测...")

    scores = []
    for i, img_path in enumerate(image_paths, 1):
        print(f"处理进度: {i}/{len(image_paths)} - {img_path.name}")
        conf = get_max_confidence(model, str(img_path))
        scores.append((img_path, conf))

    scores.sort(key=lambda x: x[1], reverse=True)
    top_k = min(args.top, len(scores))
    top_images = scores[:top_k]

    print(f"\n置信度最高的 {top_k} 张图片：")
    for img_path, conf in top_images:
        print(f"  {img_path.name}: {conf:.4f}")

    # ---------- 修改部分：用置信度命名图片 ----------
    for img_path, conf in top_images:
        ext = img_path.suffix.lower()
        # 直接以置信度作为文件名（保留原扩展名）
        new_name = f"{conf:.4f}{ext}"
        dest = output_folder / new_name

        # 处理重名（置信度相同且扩展名相同时）
        if dest.exists():
            base = f"{conf:.4f}"
            counter = 1
            while dest.exists():
                dest = output_folder / f"{base}_{counter}{ext}"
                counter += 1

        shutil.copy2(img_path, dest)
        print(f"复制完成: {img_path.name} -> {dest.name}")

    print(f"\n已完成！{top_k} 张图片已复制到 {output_folder}")

if __name__ == "__main__":
    main()