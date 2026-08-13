package com.example.speako.domain.presentation.entity;

import com.example.speako.domain.script.entity.Script;
import jakarta.persistence.*;
import lombok.*;

import java.util.ArrayList;
import java.util.List;

@Entity
@Table(name = "slides")
@Getter
@Builder // ✨ 클래스 레벨에 붙여야 @Builder.Default가 유의미하게 동작합니다.
@AllArgsConstructor // @Builder와 함께 사용하기 위해 필요
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

    @Builder.Default
    @OneToMany(mappedBy = "slide", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<Script> scripts = new ArrayList<>();
}