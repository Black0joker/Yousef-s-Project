/* Shared UI helpers: a styled confirmation dialog, auto-dismissing success
   alerts, and loading skeletons. Plain IIFE, no build step.

   Why this exists: the pages used window.confirm (blocking and unstyled) and
   left success alerts on screen until the next action. Both read as bugs. */
(function () {
  "use strict";

  var modal = null;
  var active = null; // { resolve, previous }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function buildModal() {
    var root = el("div", "modal");
    root.hidden = true;
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-labelledby", "thui-modal-title");
    root.setAttribute("aria-describedby", "thui-modal-text");

    var backdrop = el("div", "modal-backdrop");
    backdrop.setAttribute("data-thui-cancel", "");

    var card = el("div", "modal-card");

    var title = el("h2", "modal-title");
    title.id = "thui-modal-title";

    var text = el("p", "modal-text");
    text.id = "thui-modal-text";

    var actions = el("div", "modal-actions");
    var cancel = el("button", "btn-secondary", "Cancel");
    cancel.type = "button";
    cancel.setAttribute("data-thui-cancel", "");

    var confirm = el("button", "btn-danger", "Confirm");
    confirm.type = "button";
    confirm.setAttribute("data-thui-confirm", "");

    actions.appendChild(cancel);
    actions.appendChild(confirm);
    card.appendChild(title);
    card.appendChild(text);
    card.appendChild(actions);
    root.appendChild(backdrop);
    root.appendChild(card);

    root.querySelectorAll("[data-thui-cancel]").forEach(function (node) {
      node.addEventListener("click", function () {
        settle(false);
      });
    });
    confirm.addEventListener("click", function () {
      settle(true);
    });
    root.addEventListener("keydown", onKeydown);
    document.body.appendChild(root);
    return root;
  }

  function onKeydown(event) {
    if (!active) return;
    if (event.key === "Escape") {
      event.preventDefault();
      settle(false);
      return;
    }
    if (event.key !== "Tab") return;
    // two controls: keep focus inside the dialog
    var focusable = modal.querySelectorAll("button");
    if (!focusable.length) return;
    var first = focusable[0];
    var last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function settle(result) {
    if (!active) return;
    var resolve = active.resolve;
    var previous = active.previous;
    active = null;
    modal.hidden = true;
    if (previous && typeof previous.focus === "function") previous.focus();
    resolve(Boolean(result));
  }

  function confirm(options) {
    var opts = options || {};
    if (!modal) modal = buildModal();

    modal.querySelector("#thui-modal-title").textContent = opts.title || "Are you sure?";
    var textNode = modal.querySelector("#thui-modal-text");
    textNode.textContent = opts.message || "";
    textNode.hidden = !opts.message;

    var cancel = modal.querySelector("[data-thui-cancel]:not(.modal-backdrop)");
    var confirmButton = modal.querySelector("[data-thui-confirm]");
    cancel.textContent = opts.cancelLabel || "Cancel";
    confirmButton.textContent = opts.confirmLabel || "Confirm";

    // Capture the trigger before we move focus into the dialog.
    var previous = document.activeElement;

    modal.hidden = false;
    // Destructive actions must not be one stray Enter away.
    var target = opts.danger === false ? confirmButton : cancel;
    target.focus();

    if (active) {
      // a second call replaces the first one
      active.resolve(false);
    }
    return new Promise(function (resolve) {
      active = { resolve: resolve, previous: previous };
    });
  }

  var successTimers = new WeakMap();

  function showSuccess(node, message) {
    if (!node) return;
    node.textContent = message;
    node.classList.remove("hidden", "alert-dismissing");
    node.classList.add("anim-slide-down");

    var pending = successTimers.get(node);
    if (pending) window.clearTimeout(pending);

    var timer = window.setTimeout(function () {
      node.classList.add("alert-dismissing");
      timer = window.setTimeout(function () {
        node.classList.add("hidden");
        node.classList.remove("alert-dismissing", "anim-slide-down");
      }, 260);
      successTimers.set(node, timer);
    }, 4500);
    successTimers.set(node, timer);
  }

  function skeletonLine(className) {
    return el("span", "skeleton-line" + (className ? " " + className : ""));
  }

  function skeletons(list, rows) {
    if (!list) return;
    var count = rows || 2;
    list.textContent = "";
    for (var index = 0; index < count; index += 1) {
      var row = el("li", "skeleton-row");
      row.setAttribute("aria-hidden", "true");
      var stack = el("div", "skeleton-stack");
      stack.appendChild(skeletonLine());
      stack.appendChild(skeletonLine("skeleton-line--short"));
      var chip = el("span", "skeleton-line skeleton-line--tiny");
      chip.style.width = "3.5rem";
      chip.style.flexShrink = "0";
      row.appendChild(stack);
      row.appendChild(chip);
      list.appendChild(row);
    }
  }

  window.THUI = {
    confirm: confirm,
    showSuccess: showSuccess,
    skeletons: skeletons,
  };
})();