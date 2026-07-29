package com.example.speako.domain.presentation.entity;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Entity
@Table(name = "slides")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Slide {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "slide_id")
    private Long slideId;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "presentation_id", nullable = false)
    private Presentation presentation;

    @Column(name = "slide_order", nullable = false)
    private int slideOrder;

    @Column(name = "slide_title")
    private String slideTitle;

    @Column(name = "raw_text", columnDefinition = "TEXT")
    private String rawText;

    @Column(name = "thumbnail_url", length = 2083)
    private String thumbnailUrl;

    @Builder
    public Slide(Presentation presentation, int slideOrder, String slideTitle, String rawText, String thumbnailUrl) {
        this.presentation = presentation;
        this.slideOrder = slideOrder;
        this.slideTitle = slideTitle;
        this.rawText = rawText;
        this.thumbnailUrl = thumbnailUrl;
    }
}