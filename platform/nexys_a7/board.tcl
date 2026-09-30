# Both preserved XCI files require this exact board identity. No silent
# fallback to an unset board and no implicit network download.
set board_id digilentinc.com:nexys-a7-50t:part0:1.3
set board_repos [list]
if {[info exists ::env(NEXYS_BOARD_REPO)]} {
    lappend board_repos [file normalize $::env(NEXYS_BOARD_REPO)]
}
if {[info exists ::env(APPDATA)]} {
    set candidate [file join $::env(APPDATA) Xilinx Vivado [version -short] xhub board_store xilinx_board_store]
    if {[file isdirectory $candidate]} { lappend board_repos $candidate }
}
foreach var {HOME USERPROFILE} {
    if {[info exists ::env($var)]} {
        set candidate [file join $::env($var) .Xilinx Vivado [version -short] xhub board_store xilinx_board_store]
        if {[file isdirectory $candidate]} { lappend board_repos $candidate }
    }
}
if {[llength $board_repos]} { set_property board_part_repo_paths $board_repos [current_project] }
if {[llength [get_board_parts -quiet $board_id]] != 1} {
    error "Install Nexys A7-50T board files version 1.3 or set NEXYS_BOARD_REPO; required board: $board_id"
}
set_property board_part $board_id [current_project]
if {[get_property board_part [current_project]] ne $board_id} { error "Board identity did not apply" }
