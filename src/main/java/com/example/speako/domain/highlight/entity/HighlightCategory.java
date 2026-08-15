package com.example.speako.domain.highlight.entity;

import lombok.Getter;
import lombok.RequiredArgsConstructor;

@Getter
@RequiredArgsConstructor
public enum HighlightCategory {
    MISMATCH("mismatch", "불일치"),
    LIAISON("liaison", "연음"),
    LENGTH("length", "장단음");

    private final String code;
    private final String description;
}