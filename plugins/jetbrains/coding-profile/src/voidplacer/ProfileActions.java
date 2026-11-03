package voidplacer;

import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.fileEditor.ex.FileEditorManagerEx;
import com.intellij.openapi.fileEditor.impl.EditorWindow;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.wm.ToolWindow;
import com.intellij.openapi.wm.ToolWindowAnchor;
import com.intellij.openapi.wm.ToolWindowManager;
import com.intellij.openapi.wm.ex.ToolWindowManagerEx;

import java.awt.Component;
import java.awt.KeyboardFocusManager;
import java.awt.Point;
import java.util.ArrayList;
import java.util.List;

/** Small IDE actions for shortcuts missing from JetBrains' public keymap. */
public final class ProfileActions {
  private ProfileActions() {}

  public static final class MoveRight extends Move {
    public MoveRight() { super(ToolWindowAnchor.RIGHT); }
  }

  public static final class MoveBottom extends Move {
    public MoveBottom() { super(ToolWindowAnchor.BOTTOM); }
  }

  private abstract static class Move extends AnAction {
    private final ToolWindowAnchor anchor;
    Move(ToolWindowAnchor anchor) { this.anchor = anchor; }

    @Override public void actionPerformed(AnActionEvent event) {
      Project project = event.getProject();
      if (project == null) return;
      ToolWindowManager manager = ToolWindowManager.getInstance(project);
      String id = manager.getActiveToolWindowId();
      if (id == null) id = manager.getLastActiveToolWindowId();
      ToolWindow window = id == null ? null : manager.getToolWindow(id);
      if (window != null) {
        window.setAnchor(anchor, null);
        window.activate(null);
      }
    }
  }

  public static final class Left extends Navigate { public Left() { super(-1, 0); } }
  public static final class Down extends Navigate { public Down() { super(0, 1); } }
  public static final class Up extends Navigate { public Up() { super(0, -1); } }
  public static final class Right extends Navigate { public Right() { super(1, 0); } }

  private abstract static class Navigate extends AnAction {
    private final int dx, dy;
    Navigate(int dx, int dy) { this.dx = dx; this.dy = dy; }

    @Override public void actionPerformed(AnActionEvent event) {
      Project project = event.getProject();
      if (project == null) return;
      Component focus = KeyboardFocusManager.getCurrentKeyboardFocusManager().getFocusOwner();
      FileEditorManagerEx editors = FileEditorManagerEx.getInstanceEx(project);
      List<Target> targets = new ArrayList<>();
      for (EditorWindow editor : editors.getWindows()) {
        Component view = editor.getTabbedPane().getComponent();
        if (view.isShowing()) targets.add(new Target(view, () -> editor.requestFocus(true), editor, null));
      }
      for (ToolWindow window : ToolWindowManagerEx.getInstanceEx(project).getToolWindows()) {
        Component view = window.getComponent();
        if (window.isVisible() && view.isShowing())
          targets.add(new Target(view, () -> window.activate(null), null, window));
      }
      Target current = null;
      for (Target target : targets) {
        if (focus != null && javax.swing.SwingUtilities.isDescendingFrom(focus, target.view)) {
          current = target;
          break;
        }
      }
      if (current == null && focus != null && javax.swing.SwingUtilities.isDescendingFrom(focus, editors.getComponent())) {
        EditorWindow window = editors.getCurrentWindow();
        for (Target target : targets) if (target.editor == window) { current = target; break; }
      }
      if (current == null) return;
      Point origin = center(current.view);
      Target best = null;
      double score = Double.POSITIVE_INFINITY;
      for (Target target : targets) {
        if (target == current) continue;
        Point point = center(target.view);
        int forward = dx != 0 ? (point.x - origin.x) * dx : (point.y - origin.y) * dy;
        if (forward <= 8) continue;
        int cross = dx != 0 ? Math.abs(point.y - origin.y) : Math.abs(point.x - origin.x);
        double candidate = forward + cross * 2.0;
        if (candidate < score) { score = candidate; best = target; }
      }
      if (best != null) { best.focus.run(); return; }
      // When there is no pane in that direction, horizontal keys browse tabs.
      if (dx != 0 && current.editor != null) {
        int count = current.editor.getTabbedPane().getTabCount();
        if (count > 1) {
          int selected = current.editor.getTabbedPane().getSelectedIndex();
          current.editor.getTabbedPane().setSelectedIndex((selected + dx + count) % count);
          current.editor.requestFocus(true);
        }
      } else if (dy != 0 && current.tool != null) {
        // Cycle the tool windows sharing a side, including hidden ones.
        ToolWindowAnchor side = current.tool.getAnchor();
        List<ToolWindow> sideWindows = new ArrayList<>();
        if (side == ToolWindowAnchor.RIGHT) {
          for (String id : List.of("Project", "Terminal", "Run", "Problems View", "Problems")) {
            ToolWindow tool = ToolWindowManager.getInstance(project).getToolWindow(id);
            if (tool != null && tool.isAvailable() && tool.getAnchor() == side) sideWindows.add(tool);
          }
        } else {
          for (ToolWindow tool : ToolWindowManagerEx.getInstanceEx(project).getToolWindows())
            if (tool.isAvailable() && tool.getAnchor() == side) sideWindows.add(tool);
        }
        int index = sideWindows.indexOf(current.tool);
        if (index >= 0 && sideWindows.size() > 1)
          sideWindows.get((index + dy + sideWindows.size()) % sideWindows.size()).activate(null);
      } else if (dy != 0 && current.editor != null) {
        ToolWindow terminal = ToolWindowManager.getInstance(project).getToolWindow("Terminal");
        if (terminal != null && terminal.isAvailable()) terminal.activate(null);
      }
    }

    private static Point center(Component component) {
      Point point = component.getLocationOnScreen();
      return new Point(point.x + component.getWidth() / 2, point.y + component.getHeight() / 2);
    }

    private record Target(Component view, Runnable focus, EditorWindow editor, ToolWindow tool) {}
  }

}
