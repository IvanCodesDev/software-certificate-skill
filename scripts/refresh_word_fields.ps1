param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [switch]$Visible
)

$ErrorActionPreference = 'Stop'
$resolved = (Resolve-Path -LiteralPath $Path).Path
$word = $null
$doc = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = [bool]$Visible
    $word.DisplayAlerts = 0
    $doc = $word.Documents.Open($resolved, $false, $false)
    foreach ($toc in $doc.TablesOfContents) { $toc.Update() | Out-Null }
    foreach ($story in $doc.StoryRanges) {
        $current = $story
        while ($null -ne $current) {
            $current.Fields.Update() | Out-Null
            $current = $current.NextStoryRange
        }
    }
    $doc.Repaginate()
    $pages = $doc.ComputeStatistics(2)
    $doc.Save()
    Write-Output "FIELDS_UPDATED=$resolved"
    Write-Output "PAGES=$pages TOC_COUNT=$($doc.TablesOfContents.Count)"
}
finally {
    if ($null -ne $doc) { $doc.Close($false) }
    if ($null -ne $word) { $word.Quit() }
    if ($null -ne $doc) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($doc) }
    if ($null -ne $word) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
