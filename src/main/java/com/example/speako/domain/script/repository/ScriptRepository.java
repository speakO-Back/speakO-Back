package com.example.speako.domain.script.repository;

import com.example.speako.domain.presentation.entity.Slide;
import com.example.speako.domain.script.entity.Script;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface ScriptRepository extends JpaRepository<Script, Long> {

    // ✨ EvaluationService에서 사용할 Fetch Join 메서드
    @Query("SELECT s FROM Script s JOIN FETCH s.slide sl JOIN FETCH sl.presentation p WHERE s.scriptId = :scriptId")
    Optional<Script> findWithSlideAndPresentation(@Param("scriptId") Long scriptId);

    @Modifying
    @Query("DELETE FROM Script s WHERE s.slide = :slide")
    void deleteBySlide(@Param("slide") Slide slide);
}