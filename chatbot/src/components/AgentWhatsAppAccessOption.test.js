import { fireEvent, render, screen } from "@testing-library/react";

import AgentWhatsAppAccessOption from "./AgentWhatsAppAccessOption";


test("does not render before the workspace has WhatsApp access", () => {
  const { container } = render(
    <AgentWhatsAppAccessOption
      visible={false}
      enabled={false}
      available={false}
      onChange={() => {}}
    />
  );

  expect(container).toBeEmptyDOMElement();
});

test("allows a configured company to enable WhatsApp for the agent", () => {
  const onChange = jest.fn();
  render(
    <AgentWhatsAppAccessOption
      visible
      enabled={false}
      available
      phoneNumber="+94771234567"
      onChange={onChange}
    />
  );

  const checkbox = screen.getByRole("checkbox", { name: /WhatsApp access/i });
  expect(checkbox).toBeEnabled();
  expect(screen.getByText("+94771234567")).toBeInTheDocument();
  fireEvent.click(checkbox);
  expect(onChange).toHaveBeenCalledWith(true);
});

test("requires a configured number before access can be enabled", () => {
  render(
    <AgentWhatsAppAccessOption
      visible
      enabled={false}
      available={false}
      onChange={() => {}}
    />
  );

  expect(screen.getByRole("checkbox", { name: /WhatsApp access/i })).toBeDisabled();
  expect(screen.getByText("Number required")).toBeInTheDocument();
});

test("lets an admin disable stale access after workspace configuration is removed", () => {
  const onChange = jest.fn();
  render(
    <AgentWhatsAppAccessOption
      visible
      enabled
      available={false}
      onChange={onChange}
    />
  );

  const checkbox = screen.getByRole("checkbox", { name: /WhatsApp access/i });
  expect(checkbox).toBeEnabled();
  expect(screen.getByText("Configuration removed")).toBeInTheDocument();
  fireEvent.click(checkbox);
  expect(onChange).toHaveBeenCalledWith(false);
});
