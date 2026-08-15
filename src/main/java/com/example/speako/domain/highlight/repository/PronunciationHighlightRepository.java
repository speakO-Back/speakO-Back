package com.example.speako.domain.highlight.repository;

import com.example.speako.domain.highlight.entity.PronunciationHighlight;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.util.List;

public interface PronunciationHighlightRepository extends JpaRepository<PronunciationHighlight, Long> {

    // presentationId에 속한 모든 스크립트의 하이라이트를 한 번에 조회
    @Query("SELECT h FROM PronunciationHighlight h JOIN h.script s JOIN s.slide sl WHERE sl.presentation.presentationId = :presentationId")
    List<PronunciationHighlight> findByPresentationId(@Param("presentationId") Long presentationId);
}