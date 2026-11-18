from tabvio_agent.browser.models import Bounds, Element


class ElementFactory:
    @staticmethod
    def create_element(index: int, raw_element: dict) -> Element:
        element_bounds = Bounds(**raw_element["boundingRectangle"])
        return Element(
            index,
            raw_element["signature"],
            raw_element["tag"],
            raw_element["text"],
            raw_element["attrs"],
            raw_element["cx"],
            raw_element["cy"],
            element_bounds,
        )
