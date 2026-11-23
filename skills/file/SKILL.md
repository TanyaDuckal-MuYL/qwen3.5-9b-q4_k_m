---
name: file
description: 本机文件读写列删
triggers: 文件|目录|文件夹|读一下|写一个|保存|列出|删除文件|打开文件
tools: fs
deterministic: false
---
fs 的 write/delete 会请求用户确认，被拒绝就改道或放弃，不许绕过。
read 用 offset/limit 取片段，不要一次倒全文件。路径越界报错时换沙箱内路径。