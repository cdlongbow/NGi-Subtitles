# NGi-Subtitles

MDC-NGi 字幕池。下载字幕请进入 [Subtitles](Subtitles)，程序可直接读取根目录的 [tree.json](tree.json)。

## 上传字幕

1. `jzdxjk` 从本仓库创建自己的 Fork，并从最新的 `main` 新建分支。
2. 将字幕打包成 ZIP，在 Fork 的 [update](update) 目录上传，目录下可以一次放多个 ZIP。
3. 提交 Pull Request 到本仓库的 `main`。PR 只需新增 `update/` 下的 ZIP。GitHub Actions 校验通过后会自动合并并入库。

字幕文件名使用 `番号前缀-数字.后缀`，分集字幕可加上 `-CD1`、`-CD2` 等分集标记。ZIP 可以包含子目录，入库时会按文件名归类。前缀和分集标记转为大写，后缀转为小写，数字和前导零保留。无关文件会被跳过，每个 ZIP 至少要包含一个符合命名格式的字幕。

- `abcdefg-12345.SRT` → `Subtitles/A/ABCDEFG/ABCDEFG-12345.srt`
- `ABCDEFG-12345.ass` → `Subtitles/A/ABCDEFG/ABCDEFG-12345.ass`
- `1abc-00001.srt` → `Subtitles/1/1ABC/1ABC-00001.srt`
- `fc2-4990459-cd2.SRT` → `Subtitles/F/FC2/FC2-4990459-CD2.srt`

同一番号的各分集和不同后缀可共存。内容相同的同名文件会去重。已有同名文件的内容不同时，上传会报告冲突并保留已入库版本。单个 ZIP 最大 50 MiB，解压后合计最大 128 MiB、最多 10,000 项，单个字幕最大 16 MiB。

成功处理的 ZIP 会移出 `update/`，原提交仍可在 Git 历史中查看。管理员直接上传到 `main` 也会触发入库。此时如果有 ZIP 处理失败，Actions 日志会显示原因，失败的 ZIP 会留在 `update/`，同批校验通过的 ZIP 照常入库。

## 字幕索引

`tree.json` 按首字符、番号前缀、字幕文件名分组。例如：

```json
{
  "A": {
    "ABCDEFG": [
      "ABCDEFG-12345.ass",
      "ABCDEFG-12345.srt"
    ]
  }
}
```

读取时按 `Subtitles/{首字符}/{番号前缀}/{文件名}` 拼接路径。每次入库只更新有变动的分组，索引始终包含全部已入库字幕。管理员可在 [Actions](https://github.com/li-peifeng/NGi-Subtitles/actions/workflows/subtitle-pool.yml) 手动运行字幕入库。勾选「从 Subtitles 全量重建索引」可按当前目录重新生成索引。
