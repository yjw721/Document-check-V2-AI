/* 跨组件未保存改动守卫：CustomRulesTab 写入 dirty，UnifiedPage / App 在切换前读取并确认。 */
export const navGuard = {
  dirty: false,
};

export const LEAVE_CONFIRM_MSG = "当前规则组存在未保存修改，离开将丢失改动，是否确认离开？";
