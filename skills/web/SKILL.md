---
name: web
description: 联网搜索与网页正文
triggers: 搜|搜索|查一下|网上|新闻|最新|官网|多少钱|天气|联网
tools: web
deterministic: true
---
实时信息必须 search/fetch 拿真实结果，答案带来源链接；抓不到就说抓不到，禁止编造。