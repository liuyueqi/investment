#!/bin/bash
# 清理当前目录及子目录下所有 __pycache__ 文件夹

echo "正在查找并删除所有 __pycache__ 目录..."

# 统计删除的目录数量（先计算再删除）
count=$(find . -type d -name "__pycache__" | wc -l | tr -d ' ')

if [ "$count" -eq 0 ]; then
    echo "没有找到 __pycache__ 目录，无需清理。"
    exit 0
fi

# 执行删除
find . -type d -name "__pycache__" -exec rm -rf {} +

echo "已删除 $count 个 __pycache__ 目录。"